package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func DecodeColdB0RollbackRequest(body, digest string) (ColdB0RollbackRequest, error) {
	var request ColdB0RollbackRequest
	hash := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(hash[:]) != digest {
		return request, ErrConfiguration
	}
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&request) != nil || d.Decode(new(any)) != io.EOF || !request.valid() {
		return ColdB0RollbackRequest{}, ErrConfiguration
	}
	canonical, err := json.Marshal(request)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return ColdB0RollbackRequest{}, ErrConfiguration
	}
	return request, nil
}

//go:embed cold_b0_restoration.sql
var coldB0RestorationSchema string

//go:embed cold_b0_restore_witness.lua
var coldB0RestoreWitnessLua string

//go:embed cold_b0_restore_cas.lua
var coldB0RestoreCASLua string

type ColdB0RestorationState struct {
	plan  *ColdB0RollbackPlan
	phase string
}

func (s *ColdB0RestorationState) Plan() *ColdB0RollbackPlan { return s.plan }
func (s *ColdB0RestorationState) Phase() string             { return s.phase }

func decodeColdB0RollbackPlan(body, digest string) (*ColdB0RollbackPlan, error) {
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 32*1024*1024 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return nil, ErrAuthorityLost
	}
	var doc coldB0RollbackDocument
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&doc) != nil || d.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-b0-rollback/v1" || !ownershipSHA256.MatchString(doc.TargetSHA256) || !ownershipSHA256.MatchString(doc.SnapshotSHA256) || !coldSafeID.MatchString(doc.Namespace) || !coldSafeID.MatchString(doc.ShardID) || !ownershipSHA256.MatchString(doc.Request.ReversalSHA256) || !ownershipRevision.MatchString(doc.Request.SourceRevision) || !ownershipSHA256.MatchString(doc.Request.SourceReceiptSHA256) || doc.Request.B0SourceEpoch < 1 || doc.Request.RetirementEpoch <= doc.Request.B0SourceEpoch || doc.Request.RetirementEpoch > 9999999999999 || len(doc.RedisPlan) > 2048 || len(doc.FenceTaskIDs) > 2048 {
		return nil, ErrAuthorityLost
	}
	if _, ok := coldB0Cohorts[doc.Cohort]; !ok {
		return nil, ErrAuthorityLost
	}
	for i, id := range doc.FenceTaskIDs {
		if !canonicalUUID.MatchString(id) || i > 0 && doc.FenceTaskIDs[i-1] >= id {
			return nil, ErrAuthorityLost
		}
	}
	for id, entry := range doc.RedisPlan {
		if !canonicalUUID.MatchString(id) {
			return nil, ErrAuthorityLost
		}
		if entry.Action == "drop" {
			if entry.Domain != "" || entry.WorkerType != "" || entry.FirstTime != nil || entry.Score != "" || entry.Config != nil {
				return nil, ErrAuthorityLost
			}
		} else if entry.Action != "schedule" || entry.FirstTime == nil || len(entry.Config) != 6 || !coldRollbackDomain.MatchString(entry.Domain) || (entry.WorkerType != "simple" && entry.WorkerType != "browser") || !coldRollbackScore.MatchString(entry.Score) {
			return nil, ErrAuthorityLost
		}
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return &ColdB0RollbackPlan{document: doc, body: body, digest: digest}, nil
}

func loadColdB0Restoration(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdB0RestorationState, error) {
	var body, phase string
	err := tx.QueryRow(ctx, "SELECT payload,phase FROM public.crawler_ownership_b0_restoration WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body, &phase)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	plan, err := decodeColdB0RollbackPlan(body, digest)
	if err != nil || plan.document.Request.SourceRevision != revision || (phase != "prepared" && phase != "redis-restored" && phase != "fences-cleared") {
		return nil, ErrAuthorityLost
	}
	return &ColdB0RestorationState{plan: plan, phase: phase}, nil
}

// RetainColdB0RollbackPlan re-derives the approved manifest under the existing
// barriers/row locks, and commits its immutable bytes BEFORE Redis effects.
// After an interrupted restoration use exact retained inspection/application;
// missing queue evidence is not reconstructed to rebuild a preparation.
func RetainColdB0RollbackPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, request ColdB0RollbackRequest, target *ColdB0Target, approvedDigest string) (*ColdB0RestorationState, error) {
	if c == nil || target == nil || !request.valid() || !ownershipSHA256.MatchString(approvedDigest) {
		return nil, ErrConfiguration
	}
	var result *ColdB0RestorationState
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		plan, err := deriveColdB0RollbackPlan(ctx, tx, c, request, target)
		if err != nil {
			return err
		}
		if plan.digest != approvedDigest {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, "INSERT INTO public.crawler_ownership_b0_target(target_sha256,payload) VALUES($1,$2) ON CONFLICT(target_sha256) DO NOTHING", target.digest, target.body); err != nil {
			return err
		}
		var retainedTarget string
		if err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_b0_target WHERE target_sha256=$1", target.digest).Scan(&retainedTarget); err != nil {
			return err
		}
		if retainedTarget != target.body {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, `INSERT INTO public.crawler_ownership_b0_restoration(plan_sha256,reversal_sha256,source_revision,retirement_epoch,target_sha256,payload)
 VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(reversal_sha256) DO NOTHING`, plan.digest, request.ReversalSHA256, request.SourceRevision, request.RetirementEpoch, target.digest, plan.body); err != nil {
			return err
		}
		result, err = loadColdB0Restoration(ctx, tx, approvedDigest, request.SourceRevision)
		if err != nil {
			return err
		}
		if result.plan.body != plan.body || result.phase != "prepared" {
			return ErrAuthorityLost
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// InspectColdB0Restoration observes retained progress without allocation locks,
// Redis/allocator adoption or permission to release ownership/start services.
func InspectColdB0Restoration(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdB0RestorationState, error) {
	if pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var result *ColdB0RestorationState
	err := pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		var err error
		result, err = loadColdB0Restoration(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func coldB0RestoreWitness(ctx context.Context, c *Client, target *ColdB0Target, plan *ColdB0RollbackPlan) (string, error) {
	d := plan.document
	r := d.Request
	value, err := c.redis.Eval(ctx, coldB0RestoreWitnessLua, target.keys(), d.Namespace, d.ShardID, strconv.FormatInt(r.B0SourceEpoch, 10), "go", d.Cohort, plan.digest, r.SourceReceiptSHA256).Text()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return "", ErrAuthorityLost
		}
		return "", ErrObservation
	}
	if value != "present" && value != "restored" {
		return "", ErrProtocol
	}
	return value, nil
}

func applyColdB0Restore(ctx context.Context, c *Client, target *ColdB0Target, plan *ColdB0RollbackPlan) error {
	if plan.snapshot == nil {
		return ErrAuthorityLost
	}
	redisPlan, err := json.Marshal(plan.document.RedisPlan)
	if err != nil {
		return ErrProtocol
	}
	snapshot, err := json.Marshal(plan.snapshot)
	if err != nil {
		return ErrProtocol
	}
	args := target.auditArguments(plan.document.Request.B0SourceEpoch)
	args[0] = "rollback_legacy"
	args[15] = plan.digest
	args[18] = string(redisPlan)
	args[22] = plan.document.Request.SourceReceiptSHA256
	args = append(args, string(snapshot))
	script := "local function audited_b0()\n" + target.lua + "\nend\nlocal function observed_b0()\n" + coldB0RollbackLua + "\nend\n" + coldB0RestoreCASLua
	reply, err := c.redis.Eval(ctx, script, target.keys(), args...).Slice()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return ErrAuthorityLost
		}
		return ErrObservation
	}
	if len(reply) != 12 || reply[0] != "accepted" || reply[1] != "rolled_back" {
		return ErrAuthorityLost
	}
	return nil
}

// RestoreColdB0Rollback commits actual Redis restoration + SAVE/readback before
// advancing SQL progress; a subsequent transaction removes only the retained
// source fences. Exact tombstone recovery never replays canonical callbacks.
// It deliberately leaves source journal reversing, ordinary projection and
// producer sentinel/host receipt intact: full ownership/release readiness follow.
func RestoreColdB0Rollback(ctx context.Context, pool *pgxpool.Pool, c *Client, digest, revision string, target *ColdB0Target) (*ColdB0RestorationState, error) {
	if c == nil || target == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdB0Restoration(ctx, tx, digest, revision)
		if err != nil {
			return err
		}
		plan := state.plan
		r := plan.document.Request
		if plan.document.TargetSHA256 != target.digest {
			return ErrAuthorityLost
		}
		if err := requireColdB0RollbackContext(ctx, tx, r, target); err != nil {
			return err
		}
		witness, err := coldB0RestoreWitness(ctx, c, target, plan)
		if err != nil {
			return err
		}
		if witness == "present" {
			if state.phase != "prepared" {
				return ErrAuthorityLost
			}
			fresh, err := deriveColdB0RollbackPlan(ctx, tx, c, r, target)
			if err != nil {
				return err
			}
			if fresh.digest != digest || fresh.body != plan.body {
				return ErrAuthorityLost
			}
			if err := applyColdB0Restore(ctx, c, target, fresh); err != nil {
				return err
			}
		}
		if _, err := c.redis.Save(ctx).Result(); err != nil {
			return ErrObservation
		}
		if witness, err := coldB0RestoreWitness(ctx, c, target, plan); err != nil || witness != "restored" {
			if err != nil {
				return err
			}
			return ErrAuthorityLost
		}
		if state.phase == "prepared" {
			_, err = tx.Exec(ctx, "UPDATE public.crawler_ownership_b0_restoration SET phase='redis-restored' WHERE plan_sha256=$1 AND phase='prepared'", digest)
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	var result *ColdB0RestorationState
	err = coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdB0Restoration(ctx, tx, digest, revision)
		if err != nil {
			return err
		}
		r := state.plan.document.Request
		if err := requireColdB0RollbackContext(ctx, tx, r, target); err != nil {
			return err
		}
		if witness, err := coldB0RestoreWitness(ctx, c, target, state.plan); err != nil || witness != "restored" {
			if err != nil {
				return err
			}
			return ErrAuthorityLost
		}
		rows, err := tx.Query(ctx, "SELECT job_posting_id::text FROM public.lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2 ORDER BY job_posting_id LIMIT 2049 FOR UPDATE", state.plan.document.ShardID, r.B0SourceEpoch)
		if err != nil {
			return err
		}
		actual := []string{}
		for rows.Next() {
			var id string
			if err := rows.Scan(&id); err != nil {
				rows.Close()
				return err
			}
			actual = append(actual, id)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if state.phase == "fences-cleared" {
			if len(actual) != 0 {
				return ErrAuthorityLost
			}
			result = state
			return nil
		}
		if state.phase != "redis-restored" || !reflect.DeepEqual(actual, state.plan.document.FenceTaskIDs) {
			return ErrAuthorityLost
		}
		ids := map[string]bool{}
		for id := range state.plan.document.RedisPlan {
			ids[id] = true
		}
		for _, id := range actual {
			ids[id] = true
		}
		query := []string{}
		for id := range ids {
			query = append(query, id)
		}
		sort.Strings(query)
		var leased bool
		if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM public.job_posting WHERE id=ANY($1::uuid[]) AND leased_until>now())", query).Scan(&leased); err != nil {
			return err
		}
		if leased {
			return ErrAuthorityLost
		}
		command, err := tx.Exec(ctx, "DELETE FROM public.lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2 AND job_posting_id=ANY($3::uuid[])", state.plan.document.ShardID, r.B0SourceEpoch, actual)
		if err != nil {
			return err
		}
		if command.RowsAffected() != int64(len(actual)) {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, "UPDATE public.crawler_ownership_b0_restoration SET phase='fences-cleared' WHERE plan_sha256=$1 AND phase='redis-restored'", digest); err != nil {
			return err
		}
		state.phase = "fences-cleared"
		result = state
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
