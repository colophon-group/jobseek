package queue

import (
	"context"
	_ "embed"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed first_ownership.lua
var firstOwnershipLua string

const coldPublicationKey = "crawler:ownership:transition"

//go:embed b0_audit.lua
var firstB0AuditLua []byte

// CaptureFirstOwnershipB0 uses the build's byte-checked production B0 audit.
// It observes canonical cohort configuration without a producer or queue effect.
func CaptureFirstOwnershipB0(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, namespace, shard, cohort string) (*ColdB0Target, error) {
	return CaptureColdB0Target(ctx, pool, client, epoch, namespace, shard, cohort, firstB0AuditLua)
}

// FirstOwnershipResult is a database/projection observation, not permission to
// start services. The host still owns the original flock, source/image checks,
// all-writer containment, durable pending receipt and complete stack readiness.
type FirstOwnershipResult struct {
	State               string `json:"state"`
	SourceRevision      string `json:"source_revision"`
	RoutingEpoch        int64  `json:"routing_epoch"`
	PlanSHA256          string `json:"plan_sha256"`
	ProjectionSHA1      string `json:"projection_sha1"`
	Members             int    `json:"members"`
	AdminSourceRevision string `json:"admin_source_revision,omitempty"`
	AdminImageRef       string `json:"admin_image_ref,omitempty"`
}

// ActivateFirstOwnershipInHostScope adopts the first ordinary owner without
// rotating an already proven B0 incarnation. Previously retired owners remain
// retained at older epochs. Another active owner, a retired owner at this or a
// later epoch, an attempt before current adoption or a changed epoch refuses it.
// The source-pinned B0 conservation audit runs atomically with projection CAS.
// Projection SAVE is acknowledged BEFORE SQL activation. An active exact retry
// only observes; it cannot reconstruct a missing projection or repeat SAVE.
func ActivateFirstOwnershipInHostScope(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, digest, source string, target *ColdB0Target) (*FirstOwnershipResult, error) {
	return applyFirstOwnership(ctx, pool, client, epoch, digest, source, target, false)
}

// RetireFirstOwnershipInHostScope closes that same first owner at the unchanged
// B0 epoch. In the original cold host/SQL scope it restores interrupted owned
// monitors from canonical deadlines, preserving retained attempt receipts,
// canonical rows and every B0 key. Projection
// removal and SAVE precede SQL retirement. A never-activated staged candidate
// remains staged and inert after cancellation; its retained identity is useful
// for diagnosis. A completed retirement observes only and repeats no SAVE.
func RetireFirstOwnershipInHostScope(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, digest, source string, target *ColdB0Target) (*FirstOwnershipResult, error) {
	return applyFirstOwnership(ctx, pool, client, epoch, digest, source, target, true)
}

func firstOwnershipPlan(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, digest, source string, target *ColdB0Target, retire bool) (*OwnershipPlan, string, error) {
	var plan *OwnershipPlan
	var state string
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var current int64
		var called, prior, attempts bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current != epoch {
			return ErrAuthorityLost
		}
		if err := tx.QueryRow(ctx, `SELECT
 EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE plan_sha256<>$1
   AND (state='active' OR (state='retired' AND routing_epoch>=$2))),
 EXISTS(SELECT 1 FROM public.ordinary_worker_write_fence WHERE state='active'
   AND routing_epoch>=$2)`, digest, epoch).Scan(&prior, &attempts); err != nil {
			return err
		}
		if prior {
			return ErrAuthorityLost
		}
		var body string
		err := tx.QueryRow(ctx, `SELECT payload,state FROM public.ordinary_worker_ownership_plan
 WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3`, digest, source, epoch).Scan(&body, &state)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrAuthorityLost
		}
		if err != nil {
			return err
		}
		plan, err = decodeOwnership(body, digest)
		if err != nil || plan.SourceRevision() != source || plan.Epoch() != epoch || (!retire && state == "retired") {
			return ErrAuthorityLost
		}
		// A completed exact activation retry observes the same existing owner;
		// its interrupted attempts must remain available to normal reaping and
		// recovery when the host restarts that complete, unchanged stack.
		if attempts && state == "staged" {
			return ErrAuthorityLost
		}
		if attempts {
			ids := make([]string, 0, plan.MemberCount())
			for _, member := range plan.document.Members {
				ids = append(ids, member.BoardID)
			}
			detailIDs := make([]string, 0, len(plan.document.Details))
			for _, detail := range plan.document.Details {
				detailIDs = append(detailIDs, detail.BoardID)
			}
			var foreign bool
			// Interrupted attempts remain retained after retirement. Admit an
			// older monitor only when its immutable, retired plan owned it;
			// unrelated old attempts and current/future foreign work still refuse.
			if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_write_fence f
 WHERE f.state='active' AND NOT (
 (f.task_kind='monitor' AND f.task_id=f.board_id AND (
   (f.routing_epoch=$1 AND f.board_id=ANY($2::uuid[])) OR
   (f.routing_epoch<$1 AND EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
     WHERE p.state='retired' AND p.routing_epoch=f.routing_epoch
     AND p.payload::jsonb->'members' @> jsonb_build_array(jsonb_build_object(
       'board_id',f.board_id::text,'kind','monitor','worker','simple')))))) OR
 (f.task_kind='scrape' AND (
   (f.routing_epoch=$1 AND f.board_id=ANY($3::uuid[]) AND EXISTS(SELECT 1 FROM public.job_posting jp WHERE jp.id=f.task_id AND jp.board_id=f.board_id)) OR
   (f.routing_epoch<$1 AND EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan p
     WHERE p.state='retired' AND p.routing_epoch=f.routing_epoch
     AND EXISTS(SELECT 1 FROM jsonb_array_elements(p.payload::jsonb->'details') d
       WHERE d->>'board_id'=f.board_id::text AND d->>'profile' IN ('workday.cxs-detail/v1','jsonld.direct-detail/v1') AND d->>'worker'='simple')))))))`, epoch, ids, detailIDs).Scan(&foreign); err != nil {
				return err
			}
			if foreign {
				return ErrAuthorityLost
			}
		}
		if !retire {
			authority := &Authority{queue: client}
			for _, member := range plan.document.Members {
				profile, _, err := authority.observeGreenhouseMonitor(ctx, tx, member.BoardID)
				if err != nil {
					return err
				}
				if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
					return ErrAuthorityLost
				}
			}
		}

		if !retire {
			for _, detail := range plan.document.Details {
				profile, _, err := (&Authority{queue: client}).observeDetailOwnershipState(ctx, tx, detail.BoardID, false)
				if err != nil {
					return err
				}
				binding, err := plan.detailContext(detail)
				if err != nil {
					return err
				}
				if profile.Profile != detail.Profile || profile.Domain != detail.Domain || profile.CompanyID != binding.CompanyID || profile.EffectiveBoardSHA256 != binding.EffectiveConfigHash {
					return ErrAuthorityLost
				}
			}
		}
		return target.attest(ctx, tx, client, plan)
	})
	return plan, state, err
}

func firstOwnershipProjection(ctx context.Context, client *Client, epoch int64, plan *OwnershipPlan, target *ColdB0Target, operation, retirement string) error {
	keys := append(target.keys(), ownershipProjectionKey, coldPublicationKey, "inflight:simple")
	args := append(target.auditArguments(epoch), operation, plan.body, retirement, plan.projection)
	script := "local function audited_b0()\n" + target.lua + "\nend\n" + firstRetirementLua + firstOwnershipLua
	result, err := client.redis.Eval(ctx, script, keys, args...).Text()
	if err != nil {
		var refused redis.Error
		if errors.As(err, &refused) {
			return ErrAuthorityLost
		}
		return ErrObservation
	}
	if result != "accepted" {
		return ErrAuthorityLost
	}
	return nil
}

// SAVE may block for much longer than the worker's three-second read budget.
// Keep the worker transport unchanged and use one non-retrying connection to
// the same server incarnation for this bounded administrative acknowledgement.
func firstOwnershipSave(ctx context.Context, client *Client) error {
	ctx, cancel := context.WithTimeout(ctx, 120*time.Second)
	defer cancel()
	before, err := client.RedisInstanceSHA256(ctx)
	if err != nil {
		return err
	}
	options := *client.redis.Options()
	options.ReadTimeout, options.WriteTimeout = 120*time.Second, 3*time.Second
	options.MaxRetries, options.PoolSize = -1, 1
	options.MinIdleConns = 0
	saving := &Client{redis: redis.NewClient(&options)}
	defer saving.Close()
	identity, err := saving.RedisInstanceSHA256(ctx)
	if err != nil || identity != before {
		return ErrObservation
	}
	if reply, err := saving.redis.Save(ctx).Result(); err != nil || reply != "OK" {
		return ErrObservation
	}
	identity, err = saving.RedisInstanceSHA256(ctx)
	if err != nil || identity != before {
		return ErrObservation
	}
	identity, err = client.RedisInstanceSHA256(ctx)
	if err != nil || identity != before {
		return ErrObservation
	}
	return nil
}

func applyFirstOwnership(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, digest, source string, target *ColdB0Target, retire bool) (*FirstOwnershipResult, error) {
	if client == nil || target == nil || epoch < 1 || epoch > 9999999999999 || !ownershipSHA256.MatchString(digest) || CheckHostColdSQLScope(ctx, pool, source) != nil {
		return nil, ErrAuthorityLost
	}
	plan, state, err := firstOwnershipPlan(ctx, pool, client, epoch, digest, source, target, retire)
	if err != nil {
		return nil, authorityError(err)
	}
	operation := "inspect-active"
	if retire {
		operation = "inspect-retired"
	}
	completed := (!retire && state == "active") || (retire && state == "retired")
	retirement := "[]"
	if retire && state == "active" {
		retirement, err = firstRetirementMembers(ctx, pool, client, plan)
		if err != nil {
			return nil, err
		}
	}
	if !completed {
		operation = "publish"
		if retire {
			operation = "retire"
		}
	}
	if err := firstOwnershipProjection(ctx, client, epoch, plan, target, operation, retirement); err != nil {
		return nil, err
	}
	if !completed {
		if err := firstOwnershipSave(ctx, client); err != nil {
			return nil, err
		}
		// Revalidate canonical eligibility, current epoch, held barriers and
		// the exact projection AFTER durable Redis publication, before SQL.
		fresh, actual, err := firstOwnershipPlan(ctx, pool, client, epoch, digest, source, target, retire)
		if err != nil {
			return nil, authorityError(err)
		}
		if actual != state || fresh.body != plan.body {
			return nil, ErrAuthorityLost
		}
		inspect := "inspect-active"
		if retire {
			inspect = "inspect-retired"
		}
		if err := firstOwnershipProjection(ctx, client, epoch, plan, target, inspect, "[]"); err != nil {
			return nil, err
		}
		if (!retire && state == "staged") || (retire && state == "active") {
			next := "active"
			if retire {
				next = "retired"
			}
			if err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
				tag, err := tx.Exec(ctx, "UPDATE public.ordinary_worker_ownership_plan SET state=$1 WHERE plan_sha256=$2 AND state=$3", next, digest, state)
				if err != nil {
					return err
				}
				if tag.RowsAffected() != 1 {
					return ErrAuthorityLost
				}
				return nil
			}); err != nil {
				return nil, authorityError(err)
			}
			state = next
		}
	}
	if CheckHostColdSQLScope(ctx, pool, source) != nil {
		return nil, ErrAuthorityLost
	}
	return &FirstOwnershipResult{State: state, SourceRevision: source, RoutingEpoch: epoch, PlanSHA256: digest, ProjectionSHA1: plan.ProjectionSHA1(), Members: plan.MemberCount()}, nil
}
