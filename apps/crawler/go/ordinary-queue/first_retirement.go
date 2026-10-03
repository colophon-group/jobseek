package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed first_retirement.lua
var firstRetirementLua string

type firstRetirementMember struct {
	BoardID     string            `json:"board_id"`
	Domain      string            `json:"domain"`
	Due         *string           `json:"due"`
	Completed   bool              `json:"completed"`
	LearnedHost string            `json:"learned_host"`
	Config      map[string]string `json:"config"`
}

// Capture authoritative deadlines while the host's original exclusive SQL
// session is live and every runtime writer is stopped. Retained receipts are
// read, never deleted, rewritten or reported as completed for an aborted fetch.
// The same atomic B0/projection CAS then preflights and restores owned Redis
// representations before SAVE and SQL owner retirement. A process resuming an
// old fetch loses both its lease token and durable active-plan authority.
func firstRetirementMembers(ctx context.Context, pool *pgxpool.Pool, client *Client, plan *OwnershipPlan) (string, error) {
	if CheckHostColdSQLScope(ctx, pool, plan.SourceRevision()) != nil {
		return "", ErrAuthorityLost
	}
	members := make([]firstRetirementMember, 0, plan.MemberCount())
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		authority := &Authority{queue: client}
		for _, member := range plan.document.Members {
			profile, config, err := authority.observeGreenhouseMonitorState(ctx, tx, member.BoardID, true)
			if err != nil {
				return err
			}
			if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
				return ErrAuthorityLost
			}
			var due *time.Time
			var state, digest, learned string
			err = tx.QueryRow(ctx, `SELECT CASE WHEN b.is_enabled THEN b.next_check_at END,
 COALESCE(f.state,''),COALESCE(f.config_sha256,''),COALESCE(f.learned_egress_host,'')
 FROM public.job_board b LEFT JOIN public.ordinary_worker_write_fence f
 ON f.task_kind='monitor' AND f.task_id=b.id AND f.routing_epoch=$2
 WHERE b.id=$1::uuid`, member.BoardID, plan.Epoch()).Scan(&due, &state, &digest, &learned)
			if err != nil {
				return err
			}
			row := firstRetirementMember{BoardID: member.BoardID, Domain: member.Domain, Completed: state == "completed", Config: config}
			if due != nil {
				if !validTime(seconds(*due)) {
					return ErrAuthorityLost
				}
				value := number(seconds(*due))
				row.Due = &value
			}
			// Propagate a committed host observation only across the exact
			// pre-settlement config. If settlement already published it, retain
			// that current projection without replaying historical evidence.
			if row.Completed && digest == configDigest(config) && learned != "" {
				if !validPart(learned) || len(learned) > 253 {
					return ErrAuthorityLost
				}
				row.LearnedHost = learned
			}
			members = append(members, row)
		}
		return nil
	})
	if err != nil {
		return "", authorityError(err)
	}
	body, err := json.Marshal(members)
	if err != nil || len(body) > 32<<20 {
		return "", ErrAuthorityLost
	}
	return string(body), nil
}
