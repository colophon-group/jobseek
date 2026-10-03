package queue

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// This is a fresh SQL/Redis observation, not permission to remove a sentinel,
// stop a process, clear a tombstone or start the reserved retirement owner.
type ColdB0CleanupEvidence struct {
	body, digest string
}

func (e *ColdB0CleanupEvidence) Payload() string { return e.body }
func (e *ColdB0CleanupEvidence) SHA256() string  { return e.digest }

type coldB0CleanupDocument struct {
	Version                       string             `json:"version"`
	Binding                       HostColdSQLBinding `json:"binding"`
	HostSQL                       json.RawMessage    `json:"host_sql"`
	HostSQLSHA256                 string             `json:"host_sql_sha256"`
	RedisInstanceSHA256           string             `json:"redis_instance_sha256"`
	OrdinaryRestorationPlanSHA256 string             `json:"ordinary_restoration_plan_sha256"`
	B0RestorationPlanSHA256       string             `json:"b0_restoration_plan_sha256"`
	ReversalSHA256                string             `json:"reversal_sha256"`
	TargetSHA256                  string             `json:"target_sha256"`
	Namespace                     string             `json:"namespace"`
	ShardID                       string             `json:"shard_id"`
	Cohort                        string             `json:"cohort"`
	SourceEpoch                   int64              `json:"source_epoch"`
	RetirementEpoch               int64              `json:"retirement_epoch"`
	SourceReceiptSHA256           string             `json:"source_receipt_sha256"`
	SourceGoWriteFences           int64              `json:"source_go_write_fences"`
	RuntimeAdmission              bool               `json:"runtime_admission"`
}

// ObserveColdB0Cleanup requires the live exclusive host SQL backend. It freshly
// joins the retained ordinary/B0 decisions, reserved reversal, exact allocator
// and restored tombstone, then counts source Go fences without clearing them.
// The caller still must independently prove producer stop, sentinel/release
// identity, host lock and all-writer containment before delegating cleanup.
func ObserveColdB0Cleanup(ctx context.Context, pool *pgxpool.Pool, c *Client, ordinarySHA, source string, target *ColdB0Target) (*ColdB0CleanupEvidence, error) {
	if ctx == nil || c == nil || target == nil || !ownershipSHA256.MatchString(ordinarySHA) || !ownershipRevision.MatchString(source) {
		return nil, ErrConfiguration
	}
	if CheckHostColdSQLScope(ctx, pool, source) != nil {
		return nil, ErrAuthorityLost
	}
	scope := ctx.Value(hostColdSQLKey{}).(*HostColdSQL)
	sqlBody, sqlSHA := scope.Body(), scope.SHA256()
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	instance, err := c.RedisInstanceSHA256(ctx)
	if err != nil {
		return nil, err
	}
	var document coldB0CleanupDocument
	err = coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		ordinary, err := loadColdOrdinaryRestoration(ctx, tx, ordinarySHA, source)
		if err != nil {
			return err
		}
		// Re-derive without effects: a retained digest alone cannot authorize
		// changed current boards, leases, reversal state or restoration source.
		current, err := deriveColdOrdinaryRestoration(ctx, tx, c, ordinary.Request(), target)
		if err != nil {
			return err
		}
		if current.body != ordinary.body || current.digest != ordinary.digest {
			return ErrAuthorityLost
		}
		b0, err := loadColdB0Restoration(ctx, tx, ordinary.Request().B0RestorationPlanSHA256, source)
		if err != nil {
			return err
		}
		if b0.phase != "fences-cleared" || b0.plan.document.TargetSHA256 != target.digest {
			return ErrAuthorityLost
		}
		request := b0.plan.Request()
		var fences int64
		if err := tx.QueryRow(ctx, "SELECT count(*) FROM public.lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2", b0.plan.document.ShardID, request.B0SourceEpoch).Scan(&fences); err != nil {
			return err
		}
		if fences != 0 {
			return ErrAuthorityLost
		}
		// A tombstone names retired native authority. It must not expire while
		// the coordinator is building its protected cleanup handoff.
		if ttl, err := c.redis.Do(ctx, "PTTL", "lightpanda-b0:producer-owner").Int64(); err != nil || ttl != -1 {
			if err != nil {
				return ErrObservation
			}
			return ErrAuthorityLost
		}
		document = coldB0CleanupDocument{Version: "jobseek.crawler.cold-b0-cleanup-evidence/v1", Binding: scope.binding, HostSQL: json.RawMessage(sqlBody), HostSQLSHA256: sqlSHA, RedisInstanceSHA256: instance, OrdinaryRestorationPlanSHA256: ordinary.digest, B0RestorationPlanSHA256: b0.plan.digest, ReversalSHA256: request.ReversalSHA256, TargetSHA256: target.digest, Namespace: target.document.Namespace, ShardID: target.document.ShardID, Cohort: target.document.Cohort, SourceEpoch: request.B0SourceEpoch, RetirementEpoch: request.RetirementEpoch, SourceReceiptSHA256: request.SourceReceiptSHA256, SourceGoWriteFences: fences}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if CheckHostColdSQLBinding(ctx, pool, document.Binding) != nil {
		return nil, ErrAuthorityLost
	}
	currentInstance, err := c.RedisInstanceSHA256(ctx)
	if err != nil || currentInstance != instance {
		return nil, ErrAuthorityLost
	}
	body, err := json.Marshal(document)
	if err != nil {
		return nil, ErrObservation
	}
	return &ColdB0CleanupEvidence{string(body), coldForwardBytesDigest(string(body))}, nil
}
