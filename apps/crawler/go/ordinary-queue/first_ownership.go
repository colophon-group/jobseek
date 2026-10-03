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
	State          string `json:"state"`
	SourceRevision string `json:"source_revision"`
	RoutingEpoch   int64  `json:"routing_epoch"`
	PlanSHA256     string `json:"plan_sha256"`
	ProjectionSHA1 string `json:"projection_sha1"`
	Members        int    `json:"members"`
}

// ActivateFirstOwnershipInHostScope adopts the first ordinary owner without
// rotating an already proven B0 incarnation. It refuses any other served or
// retired ordinary plan, native attempt or changed epoch.
// The source-pinned B0 conservation audit runs atomically with projection CAS.
// Projection SAVE is acknowledged BEFORE SQL activation. An active exact retry
// only observes; it cannot reconstruct a missing projection or repeat SAVE.
func ActivateFirstOwnershipInHostScope(ctx context.Context, pool *pgxpool.Pool, client *Client, epoch int64, digest, source string, target *ColdB0Target) (*FirstOwnershipResult, error) {
	return applyFirstOwnership(ctx, pool, client, epoch, digest, source, target, false)
}

// RetireFirstOwnershipInHostScope closes that same first owner at the unchanged
// B0 epoch. No owned inflight work or active SQL fence may remain. It preserves
// schedules, completed receipts, canonical rows and every B0 key. Projection
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
 EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state<>'staged' AND plan_sha256<>$1),
 EXISTS(SELECT 1 FROM public.ordinary_worker_write_fence WHERE state='active')`, digest).Scan(&prior, &attempts); err != nil {
			return err
		}
		if prior || attempts {
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
		return target.attest(ctx, tx, client, plan)
	})
	return plan, state, err
}

func firstOwnershipProjection(ctx context.Context, client *Client, epoch int64, plan *OwnershipPlan, target *ColdB0Target, operation string) error {
	keys := append(target.keys(), ownershipProjectionKey, coldPublicationKey, "inflight:simple")
	args := append(target.auditArguments(epoch), operation, plan.body)
	script := "local function audited_b0()\n" + target.lua + "\nend\n" + firstOwnershipLua
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
	if !completed {
		operation = "publish"
		if retire {
			operation = "retire"
		}
	}
	if err := firstOwnershipProjection(ctx, client, epoch, plan, target, operation); err != nil {
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
		if err := firstOwnershipProjection(ctx, client, epoch, plan, target, inspect); err != nil {
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
	return &FirstOwnershipResult{state, source, epoch, digest, plan.ProjectionSHA1(), plan.MemberCount()}, nil
}
