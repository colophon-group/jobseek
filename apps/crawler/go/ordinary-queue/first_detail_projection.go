package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed first_detail_projection.lua
var firstDetailProjectionLua string

const coldDetailProjectionReadTimeout = 15 * time.Minute

type canonicalDetailProjection struct {
	ID, BoardID, Source, Domain, Worker, Due, Hash, Interval string
	Steps                                                    int
}

// The immutable staged plan has already selected the board's detail profile.
// Avoid trying every unrelated scraper for each row of the large provider
// backlogs. The existing inspector still validates the full config and actual
// source; the caller checks the resulting profile and ownership hash as before.
func inspectColdDetailProjectionRoute(binding ownershipDetail, config map[string]string, source string) (WorkdayDetailProfile, error) {
	switch binding.Profile {
	case "workday.cxs-detail/v1", "workday.proxy-cxs-detail/v1":
		return InspectWorkdayDetail(binding.BoardID, config, source, binding.Worker)
	case smartRecruitersDetailProfile:
		return InspectAPIDetail(binding.BoardID, config, source, binding.Worker)
	case jsonldDetailProfile, jsonldProxyDetailProfile:
		return InspectJSONLDDetail(binding.BoardID, config, source, binding.Worker)
	case "dom.direct-detail/v1", "dom.proxy-detail/v1":
		return InspectDOMDetail(binding.BoardID, config, source, binding.Worker)
	case "embedded.direct-detail/v1":
		return InspectEmbeddedDetail(binding.BoardID, config, source, binding.Worker)
	default:
		return inspectDetail(binding.BoardID, config, source, binding.Worker)
	}
}

// Repair queued legacy projections and wholly absent scheduled caches under
// the original cold session and immutable staged plan. Canonical SQL rows and
// receipts never change. Correct projections, deadletters and orphaned caches
// remain untouched. Every batch is preflighted before its first Redis effect
// and can be replayed from SQL.
func repairStagedDetailProjection(ctx context.Context, pool *pgxpool.Pool, client *Client, plan *OwnershipPlan) error {
	if plan == nil || client == nil || CheckHostColdSQLScope(ctx, pool, plan.SourceRevision()) != nil {
		return ErrAuthorityLost
	}
	var staged bool
	if err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		return tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM ordinary_worker_ownership_plan
 WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state='staged'
 AND $3=(SELECT last_value FROM lightpanda_b0_routing_epoch_seq))`, plan.SHA256(), plan.SourceRevision(), plan.Epoch()).Scan(&staged)
	}); err != nil || !staged {
		return ErrAuthorityLost
	}
	owner, err := client.redis.Get(ctx, ownershipProjectionKey).Result()
	if err == nil {
		// Publication follows every repair batch. An exact pending retry must
		// preserve that published prefix and use the existing SAVE recovery.
		if owner == plan.projection {
			return nil
		}
		return ErrAuthorityLost
	}
	if err != redis.Nil {
		return ErrObservation
	}
	bindings := make(map[string]ownershipDetail, len(plan.document.Details))
	contexts := make(map[string]ownershipMember, len(plan.document.Details))
	boards := make([]string, 0, len(plan.document.Details))
	for _, d := range plan.document.Details {
		bound, err := plan.detailContext(d)
		if err != nil {
			return err
		}
		bindings[d.BoardID] = d
		contexts[d.BoardID] = bound
		boards = append(boards, d.BoardID)
	}
	if len(boards) == 0 {
		return nil
	}
	// The original host SQL session keeps all three exclusive writer barriers.
	// Use its spare pool connection only for a read-only stream. Repeated UUID
	// pages force random heap reads and thousands of round trips at fleet scale;
	// an unordered stream visits each selected row once with bounded memory.
	// Redis effects still require the live original scope for every batch.
	readCtx, cancel := context.WithTimeout(ctx, coldDetailProjectionReadTimeout)
	defer cancel()
	return pgx.BeginTxFunc(readCtx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if CheckHostColdSQLScope(readCtx, pool, plan.SourceRevision()) != nil {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(readCtx, "SET LOCAL statement_timeout='15min'"); err != nil {
			return err
		}
		if _, err := tx.Exec(readCtx, "SET LOCAL idle_in_transaction_session_timeout='1min'"); err != nil {
			return err
		}
		rows, err := tx.Query(readCtx, `SELECT p.id::text,p.board_id::text,p.source_url,p.next_scrape_at,
 p.description_r2_hash,b.scraper_needs_browser
 FROM job_posting p JOIN job_board b ON b.id=p.board_id
 WHERE p.board_id=ANY($1::uuid[]) AND p.is_active AND b.is_enabled AND p.next_scrape_at IS NOT NULL`, boards)
		if err != nil {
			return err
		}
		defer rows.Close()
		batch := make([]canonicalDetailProjection, 0, coldDetailProjectionBatch)
		for rows.Next() {
			var row canonicalDetailProjection
			var due time.Time
			var hash *int64
			var browser bool
			if err := rows.Scan(&row.ID, &row.BoardID, &row.Source, &due, &hash, &browser); err != nil {
				return err
			}
			binding, ok := bindings[row.BoardID]
			if !ok {
				return ErrAuthorityLost
			}
			bound := contexts[row.BoardID]
			row.Worker = string(Simple)
			if browser {
				row.Worker = string(Browser)
			}
			// Cancellation must leave the canonical legacy lane usable.
			if row.Worker != string(binding.Worker) {
				return ErrUnsupportedProfile
			}
			profile, err := inspectColdDetailProjectionRoute(binding, bound.Config, row.Source)
			if err != nil || profile.Profile != binding.Profile || profile.CompanyID != bound.CompanyID || profile.EffectiveBoardSHA256 != bound.EffectiveConfigHash || !detailDomainMatches(binding.Domain, profile) {
				return ErrAuthorityLost
			}
			row.Domain, err = detailSourceDomain(binding, row.Source)
			if err != nil || due.IsZero() {
				return ErrAuthorityLost
			}
			row.Due, row.Steps = number(seconds(due)), 1
			if hash != nil {
				row.Hash = strconv.FormatInt(*hash, 10)
			}
			row.Interval = bound.Config["scrape_interval_hours"]
			if isOrderedDetailProfile(binding.Profile) {
				chain, err := InspectOrderedDetail(row.BoardID, bound.Config, row.Source, binding.Worker)
				if err != nil {
					return err
				}
				row.Steps = len(chain.Stages)
			}
			batch = append(batch, row)
			if len(batch) == coldDetailProjectionBatch {
				if err := repairColdDetailBatch(readCtx, pool, client, plan, batch); err != nil {
					return err
				}
				batch = batch[:0]
			}
		}
		if err := rows.Err(); err != nil {
			return err
		}
		if len(batch) > 0 {
			if err := repairColdDetailBatch(readCtx, pool, client, plan, batch); err != nil {
				return err
			}
		}
		return CheckHostColdSQLScope(readCtx, pool, plan.SourceRevision())
	})
}

const coldDetailProjectionBatch = 1024

func repairColdDetailBatch(ctx context.Context, pool *pgxpool.Pool, client *Client, plan *OwnershipPlan, batch []canonicalDetailProjection) error {
	if len(batch) == 0 || len(batch) > coldDetailProjectionBatch || CheckHostColdSQLScope(ctx, pool, plan.SourceRevision()) != nil {
		return ErrAuthorityLost
	}
	body, err := json.Marshal(batch)
	if err != nil {
		return ErrConfiguration
	}
	// Reuse the existing cold ready-index recomputation; no new scheduler
	// or alternate enqueue surface is introduced.
	result, err := client.redis.Eval(ctx, firstRetirementLua+firstDetailProjectionLua, nil, string(body), "inspect").Text()
	var changed []string
	if err != nil || json.Unmarshal([]byte(result), &changed) != nil {
		return ErrAuthorityLost
	}
	selected := make(map[string]bool, len(changed))
	for _, id := range changed {
		if selected[id] {
			return ErrAuthorityLost
		}
		selected[id] = true
	}
	repairs := make([]canonicalDetailProjection, 0, len(changed))
	for _, row := range batch {
		if !selected[row.ID] {
			continue
		}
		repairs = append(repairs, row)
	}
	if len(repairs) != len(changed) {
		return ErrAuthorityLost
	}
	if len(repairs) > 0 {
		if CheckHostColdSQLScope(ctx, pool, plan.SourceRevision()) != nil {
			return ErrAuthorityLost
		}
		body, err = json.Marshal(repairs)
		if err != nil {
			return ErrConfiguration
		}
		result, err = client.redis.Eval(ctx, firstRetirementLua+firstDetailProjectionLua, nil, string(body), "apply").Text()
		if err != nil || result != "ok" {
			return ErrAuthorityLost
		}
	}
	return CheckHostColdSQLScope(ctx, pool, plan.SourceRevision())
}
