package queue

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type committedReapReceipt struct {
	Token       string            `json:"token"`
	Due         string            `json:"due"`
	Config      map[string]string `json:"config"`
	LearnedHost string            `json:"learned_host"`
}
type leaseReapObservation struct {
	Expired  []string                        `json:"expired"`
	Receipts map[string]committedReapReceipt `json:"receipts"`
}

// WithOrdinaryLeaseReaping orders expired-claim recovery after native commits.
// Its bounded SQL receipts and exact Redis batch are consumed by reap_expired.lua
// in the callback while the same exclusive lease barrier remains held. The
// existing one-connection reaper pool suffices; no second transaction is opened.
func WithOrdinaryLeaseReaping(ctx context.Context, pool *pgxpool.Pool, client *redis.Client, worker string, batch int, fn func(context.Context, float64, string, string) error) error {
	if client == nil || fn == nil || (worker != "simple" && worker != "browser") || batch < 1 {
		return ErrConfiguration
	}
	return withOrdinaryLeaseRetirementTx(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		clock, err := client.Time(ctx).Result()
		if err != nil {
			return ErrObservation
		}
		now := seconds(clock)
		expired, err := client.ZRangeByScore(ctx, "inflight:"+worker, &redis.ZRangeBy{Min: "-inf", Max: number(now), Count: int64(batch)}).Result()
		if err != nil {
			return ErrObservation
		}
		observation := leaseReapObservation{Expired: expired, Receipts: map[string]committedReapReceipt{}}
		projection := ""
		if worker == "simple" || worker == "browser" {
			projection, err = observeCommittedLeaseReceipts(ctx, tx, client, &observation, worker)
			if err != nil {
				return err
			}
		}
		body, err := json.Marshal(observation)
		if err != nil || len(body) > 16<<20 {
			return ErrObservation
		}
		return fn(ctx, now, string(body), projection)
	})
}

func observeCommittedLeaseReceipts(ctx context.Context, tx pgx.Tx, client *redis.Client, observation *leaseReapObservation, worker string) (string, error) {
	candidates := []string{}
	for _, task := range observation.Expired {
		parts := strings.SplitN(task, "|", 3)
		if len(parts) == 3 && parts[0] == "monitor" && canonicalUUID.MatchString(parts[2]) {
			candidates = append(candidates, task)
		}
	}
	if len(candidates) == 0 {
		return "", nil
	}
	values, err := client.HMGet(ctx, "inflight_tokens:"+worker, candidates...).Result()
	if err != nil {
		return "", ErrObservation
	}
	tokens := map[string]string{}
	for index, value := range values {
		if token, ok := value.(string); ok {
			tokens[candidates[index]] = token
		}
	}
	// Tokenless legacy leases cannot carry a native completion receipt. Their
	// existing retry/dead-letter path needs no native schema or fleet lookup.
	if len(tokens) == 0 {
		return "", nil
	}
	if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", routingEpochBarrier); err != nil {
		return "", err
	}
	var body, digest string
	err = tx.QueryRow(ctx, `SELECT p.payload,p.plan_sha256 FROM public.ordinary_worker_ownership_plan p
 JOIN public.lightpanda_b0_routing_epoch_seq e ON e.is_called AND e.last_value=p.routing_epoch
 WHERE p.state='active'`).Scan(&body, &digest)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	plan, err := decodeOwnership(body, digest)
	if err != nil {
		return "", err
	}
	members := make(map[string]ownershipMember, len(plan.document.Members))
	for _, member := range plan.document.Members {
		members[member.BoardID] = member
	}
	a := &Authority{queue: &Client{redis: client}, epoch: plan.Epoch(), ownership: plan}
	for _, task := range observation.Expired {
		currentToken, tokenized := tokens[task]
		if !tokenized {
			continue
		}
		parts := strings.SplitN(task, "|", 3)
		if len(parts) != 3 || parts[0] != "monitor" {
			continue
		}
		member, owned := members[parts[2]]
		if !owned || string(member.Worker) != worker || parts[1] != member.Domain {
			continue
		}
		var token, configSHA, learned string
		var due *time.Time
		err := tx.QueryRow(ctx, `SELECT claim_token,config_sha256,next_due_at,COALESCE(learned_egress_host,'')
 FROM public.ordinary_worker_write_fence WHERE task_kind='monitor' AND task_id=$1::uuid
 AND routing_epoch=$2 AND state='completed'`, member.BoardID, plan.Epoch()).Scan(&token, &configSHA, &due, &learned)
		if errors.Is(err, pgx.ErrNoRows) {
			continue
		}
		if err != nil {
			return "", err
		}
		if currentToken != token {
			continue
		}
		profile, config, err := a.observeGreenhouseMonitor(ctx, tx, member.BoardID)
		if err != nil || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
			return "", ErrAuthorityLost
		}
		var canonicalDue *time.Time
		if err := tx.QueryRow(ctx, "SELECT next_check_at FROM public.job_board WHERE id=$1::uuid", member.BoardID).Scan(&canonicalDue); err != nil {
			return "", err
		}
		if due == nil || canonicalDue == nil || !due.Equal(*canonicalDue) || !validTime(seconds(*due)) {
			return "", ErrAuthorityLost
		}
		// Runtime metadata can change independently of the stable ownership
		// configuration. Publish a learned host only across its exact snapshot.
		if configSHA != configDigest(config) {
			learned = ""
		}
		if learned != "" && (!validPart(learned) || len(learned) > 253) {
			return "", ErrAuthorityLost
		}
		observation.Receipts[task] = committedReapReceipt{token, number(seconds(*due)), config, learned}
	}
	if len(observation.Receipts) == 0 {
		return "", nil
	}
	return plan.ProjectionSHA1(), nil
}
