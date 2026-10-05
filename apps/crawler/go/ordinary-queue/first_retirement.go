package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"sort"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
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
	Kind        Kind              `json:"kind,omitempty"`
	TaskID      string            `json:"task_id,omitempty"`
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
		ids := make([]string, 0, plan.MemberCount())
		for _, member := range plan.document.Members {
			ids = append(ids, member.BoardID)
		}
		snapshots, err := authority.observeOwnershipConfigs(ctx, tx, ids, true)
		if err != nil {
			return err
		}
		type deadlineObservation struct {
			due                    *time.Time
			state, digest, learned string
		}
		deadlines := make(map[string]deadlineObservation, len(ids))
		rows, err := tx.Query(ctx, `SELECT b.id::text,CASE WHEN b.is_enabled THEN b.next_check_at END,
 COALESCE(f.state,''),COALESCE(f.config_sha256,''),COALESCE(f.learned_egress_host,'')
 FROM public.job_board b LEFT JOIN public.ordinary_worker_write_fence f
 ON f.task_kind='monitor' AND f.task_id=b.id AND f.routing_epoch=$2
 WHERE b.id=ANY($1::uuid[])`, ids, plan.Epoch())
		if err != nil {
			return err
		}
		for rows.Next() {
			var id string
			var item deadlineObservation
			if err := rows.Scan(&id, &item.due, &item.state, &item.digest, &item.learned); err != nil {
				rows.Close()
				return err
			}
			deadlines[id] = item
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if len(deadlines) != len(ids) {
			return ErrAuthorityLost
		}
		for _, member := range plan.document.Members {
			pair := snapshots[member.BoardID]
			profile, config, err := inspectMonitorConfigs(member.BoardID, pair.canonical, pair.cached)
			if err != nil {
				return err
			}
			if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
				return ErrAuthorityLost
			}
			item := deadlines[member.BoardID]
			due, state, digest, learned := item.due, item.state, item.digest, item.learned
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
		details, err := firstRetirementDetails(ctx, tx, client, plan)
		if err != nil {
			return err
		}
		members = append(members, details...)
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

// Only interrupted or unsettled detail representations need restoration. The
// fleet's already acknowledged future queues and retained SQL receipts stay
// untouched. Resolve the Redis-before-SQL seam from the actual posting board.
func firstRetirementDetails(ctx context.Context, tx pgx.Tx, client *Client, plan *OwnershipPlan) ([]firstRetirementMember, error) {
	if len(plan.document.Details) == 0 {
		return nil, nil
	}
	bindings := make(map[string]ownershipDetail, len(plan.document.Details))
	boards := make([]string, 0, len(plan.document.Details))
	for _, d := range plan.document.Details {
		bindings[d.BoardID] = d
		boards = append(boards, d.BoardID)
	}

	snapshots, err := (&Authority{queue: client}).observeOwnershipConfigs(ctx, tx, boards, true)
	if err != nil {
		return nil, err
	}
	for _, binding := range plan.document.Details {
		pair := snapshots[binding.BoardID]
		profile, _, err := inspectDetailConfigs(binding.BoardID, pair.canonical, pair.cached)
		if err != nil {
			return nil, err
		}
		context, err := plan.detailContext(binding)
		if err != nil {
			return nil, err
		}
		if profile.Profile != binding.Profile || profile.Domain != binding.Domain || profile.EffectiveBoardSHA256 != context.EffectiveConfigHash || profile.CompanyID != context.CompanyID {
			return nil, ErrAuthorityLost
		}
	}
	type observation struct {
		id, board, source, state, digest, host string
		domain                                 string
		due                                    *time.Time
	}
	items := map[string]observation{}
	rows, err := tx.Query(ctx, `SELECT p.id::text,p.board_id::text,p.source_url,
 CASE WHEN p.is_active AND b.is_enabled THEN p.next_scrape_at END,
 f.state,f.config_sha256,COALESCE(f.learned_egress_host,'')
 FROM ordinary_worker_write_fence f JOIN job_posting p ON p.id=f.task_id AND p.board_id=f.board_id
 JOIN job_board b ON b.id=p.board_id
 WHERE f.task_kind='scrape' AND f.routing_epoch=$1 AND f.board_id=ANY($2::uuid[])`, plan.Epoch(), boards)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var item observation
		if err := rows.Scan(&item.id, &item.board, &item.source, &item.due, &item.state, &item.digest, &item.host); err != nil {
			rows.Close()
			return nil, err
		}
		items[item.id] = item
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return nil, err
	}
	inflight, err := client.redis.ZRange(ctx, "inflight:simple", 0, -1).Result()
	if err != nil {
		return nil, ErrObservation
	}
	for _, key := range inflight {
		parts := strings.Split(key, "|")
		if len(parts) != 3 {
			return nil, ErrAuthorityLost
		}
		if parts[0] != "scrape" {
			continue
		}
		if !canonicalUUID.MatchString(parts[2]) {
			return nil, ErrAuthorityLost
		}
		if known, ok := items[parts[2]]; ok {
			domain, err := detailSourceDomain(bindings[known.board], known.source)
			if err != nil || domain != parts[1] {
				return nil, ErrAuthorityLost
			}
			continue
		}
		var item observation
		item.id = parts[2]
		err := tx.QueryRow(ctx, `SELECT p.board_id::text,p.source_url,CASE WHEN p.is_active AND b.is_enabled THEN p.next_scrape_at END
 FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, item.id).Scan(&item.board, &item.source, &item.due)
		if errors.Is(err, pgx.ErrNoRows) {
			continue
		}
		if err != nil {
			return nil, err
		}
		if binding, owned := bindings[item.board]; owned {
			domain, err := detailSourceDomain(binding, item.source)
			if err != nil || domain != parts[1] {
				return nil, ErrAuthorityLost
			}
			items[item.id] = item
		}
	}
	ids := make([]string, 0, len(items))
	for id, item := range items {
		domain, err := detailSourceDomain(bindings[item.board], item.source)
		if err != nil {
			return nil, err
		}
		item.domain = domain
		items[id] = item
		ids = append(ids, id)
	}
	sort.Strings(ids)
	var result []firstRetirementMember
	// Probe retained receipts in bounded batches; acknowledged history must not
	// turn cold retirement into a network round trip for every fetched posting.
	for offset := 0; offset < len(ids); offset += 256 {
		end := min(offset+256, len(ids))
		batch := ids[offset:end]
		pipe := client.redis.Pipeline()
		probes := make([][3]*redis.Cmd, len(batch))
		for i, id := range batch {
			domain := items[id].domain
			probes[i] = [3]*redis.Cmd{
				pipe.Do(ctx, "ZMSCORE", "inflight:simple", "scrape|"+domain+"|"+id),
				pipe.Do(ctx, "ZMSCORE", "ft_scrapes_simple:"+domain, id),
				pipe.Do(ctx, "ZMSCORE", "scrapes_simple:"+domain, id),
			}
		}
		if _, err := pipe.Exec(ctx); err != nil {
			return nil, ErrObservation
		}
		for i, id := range batch {
			item := items[id]
			leaseCommand, firstCommand, recurringCommand := probes[i][0], probes[i][1], probes[i][2]
			lease, err := ownershipProbeScore(leaseCommand)
			if err != nil {
				return nil, err
			}
			first, err := ownershipProbeScore(firstCommand)
			if err != nil {
				return nil, err
			}
			recurring, err := ownershipProbeScore(recurringCommand)
			if err != nil {
				return nil, err
			}
			completed := item.state == "completed"
			if lease == nil && (!completed || (first == nil && recurring == nil)) {
				continue
			}
			if lease == nil && first == nil && ((item.due == nil && recurring == nil) || (item.due != nil && recurring != nil && *recurring == seconds(*item.due))) {
				continue
			}
			config, err := client.redis.HGetAll(ctx, "scrape:"+id).Result()
			if err != nil {
				return nil, ErrObservation
			}
			if config["board_id"] != item.board || config["source_url"] != item.source || config["domain"] != item.domain {
				return nil, ErrAuthorityLost
			}
			row := firstRetirementMember{BoardID: item.board, Domain: item.domain, Completed: completed, Config: config, Kind: Scrape, TaskID: id}
			if item.due != nil {
				due := seconds(*item.due)
				if !validTime(due) {
					return nil, ErrAuthorityLost
				}
				value := strconv.FormatFloat(due, 'f', -1, 64)
				row.Due = &value
			}
			if completed && item.digest == configDigest(config) && item.host != "" {
				if !validPart(item.host) || len(item.host) > 253 {
					return nil, ErrAuthorityLost
				}
				row.LearnedHost = item.host
			}
			result = append(result, row)
		}
	}
	return result, nil
}
