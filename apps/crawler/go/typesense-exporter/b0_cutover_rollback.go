package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"math"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type b0SettleReport struct {
	InflightSettled int64 `json:"inflight_settled"`
	ExpiredRequeued int64 `json:"expired_requeued"`
	DeadPreserved   int64 `json:"dead_preserved"`
}

// Queue TIME, fenced audit and the unchanged reaper decide lease expiry. The
// local monotonic deadline only bounds the original 75-second cold wait.
func settleB0ColdRollback(ctx context.Context, q *b0ColdQueue, receiptState, markerPhase string) (b0SettleReport, error) {
	report := b0SettleReport{}
	if receiptState != "active" && receiptState != "pending" {
		return report, errB0Cutover
	}
	types, err := q.namespaceTypes(ctx)
	if err != nil {
		return report, err
	}
	schema, err := q.ownerSchema(ctx)
	if err != nil {
		return report, err
	}
	if schema == b0RollbackSchema {
		if markerPhase == "unsafe" {
			return report, errB0Cutover
		}
		_, err = q.boundTombstone(ctx, "")
		return report, err
	}
	if schema != "" && schema != b0OwnerSchema {
		return report, errB0Cutover
	}
	if types[0] == "none" {
		for _, kind := range types[1:] {
			if kind != "none" {
				return report, errB0Cutover
			}
		}
		if !b0RollbackMarkerValid(receiptState, markerPhase, false) || schema != "" {
			return report, errB0Cutover
		}
		guard, err := q.client.Type(ctx, b0GuardKey).Result()
		if err != nil || guard != "none" {
			return report, errB0Cutover
		}
		return report, nil
	}
	if schema != b0OwnerSchema || !b0RollbackMarkerValid(receiptState, markerPhase, true) {
		return report, errB0Cutover
	}
	deadline := time.Now().Add(75 * time.Second)
	for iteration := 0; iteration < 96; iteration++ {
		audit, err := q.call(ctx, "audit", false)
		if err != nil {
			return report, err
		}
		dead, err := q.client.SCard(ctx, q.keys[4]).Result()
		if err != nil {
			return report, errB0Cutover
		}
		if audit.Secondary == 0 {
			report.DeadPreserved = dead
			return report, nil
		}
		reap, err := q.call(ctx, "reap_expired", false)
		if err != nil {
			return report, err
		}
		if reap.Value+reap.Secondary > 0 {
			report.InflightSettled += reap.Value + reap.Secondary
			report.ExpiredRequeued += reap.Value
			continue
		}
		earliest, err := q.client.ZRangeWithScores(ctx, q.keys[3], 0, 0).Result()
		if err != nil || len(earliest) != 1 {
			return report, errB0Cutover
		}
		member, ok := earliest[0].Member.(string)
		lease := earliest[0].Score
		if !ok || member == "" || math.IsNaN(lease) || math.IsInf(lease, 0) || math.Trunc(lease) != lease || lease < 1 || lease > float64(b0MaxEpoch) {
			return report, errB0Cutover
		}
		wait := time.Duration(int64(lease)-audit.ServerMS+1) * time.Millisecond
		remaining := time.Until(deadline)
		if wait <= 0 || remaining <= 0 || wait > remaining {
			return report, errB0Cutover
		}
		if wait > time.Second {
			wait = time.Second
		}
		if wait > remaining {
			wait = remaining
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return report, errB0Cutover
		case <-timer.C:
		}
	}
	return report, errB0Cutover
}

func clearB0ColdRollback(ctx context.Context, pool *pgxpool.Pool, q *b0ColdQueue, markerPhase string, allowAbsent bool) error {
	if markerPhase != "absent" || !capacityDigest.MatchString(q.identity.PlanDigest) {
		return errB0Cutover
	}
	present, err := q.boundTombstone(ctx, q.identity.PlanDigest)
	if err != nil || !present && !allowAbsent {
		return errB0Cutover
	}
	var remaining int64
	if pool == nil || pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE engine_owner='go' AND shard_id=$1 AND routing_epoch=$2", q.identity.Shard, q.identity.Epoch).Scan(&remaining) != nil || remaining != 0 {
		return errB0Cutover
	}
	if _, err = q.call(ctx, "clear_rollback_tombstone", allowAbsent); err != nil {
		return err
	}
	schema, err := q.ownerSchema(ctx)
	if err != nil || schema != "" {
		return errB0Cutover
	}
	return nil
}

func runB0ColdRollback(args []string) error {
	if len(args) == 0 || args[0] != "settle-rollback" && args[0] != "clear-rollback-tombstone" || os.Getenv("LIGHTPANDA_B0_PRODUCER_MODE") != "off" {
		return errB0Cutover
	}
	command := args[0]
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	cohort := flags.String("cohort", "", "")
	receipt := flags.String("source-receipt-sha256", "", "")
	state, plan, allowAbsent := "", "", false
	if command == "settle-rollback" {
		flags.StringVar(&state, "receipt-state", "", "")
	} else {
		flags.StringVar(&plan, "rollback-plan-digest", "", "")
		flags.BoolVar(&allowAbsent, "allow-absent", false, "")
	}
	if flags.Parse(args[1:]) != nil || flags.NArg() != 0 {
		return errB0Cutover
	}
	epoch, err := strconv.ParseInt(os.Getenv("LIGHTPANDA_B0_ROUTING_EPOCH"), 10, 64)
	if err != nil || strconv.FormatInt(epoch, 10) != os.Getenv("LIGHTPANDA_B0_ROUTING_EPOCH") {
		return errB0Cutover
	}
	identity := b0CutoverIdentity{Namespace: os.Getenv("LIGHTPANDA_B0_QUEUE_NAMESPACE"), Shard: os.Getenv("LIGHTPANDA_B0_SHARD_ID"), Cohort: *cohort, Epoch: epoch, SourceReceipt: *receipt, PlanDigest: plan}
	if command == "settle-rollback" && state != "active" && state != "pending" || command == "clear-rollback-tombstone" && !capacityDigest.MatchString(plan) {
		return errB0Cutover
	}
	ctx, cancel := context.WithTimeout(context.Background(), 80*time.Second)
	defer cancel()
	url := os.Getenv("REDIS_URL")
	if url == "" {
		url = "redis://localhost:6379/0"
	}
	options, err := redis.ParseURL(url)
	if err != nil {
		return errB0Cutover
	}
	options.MaxRetries = -1
	options.DialTimeout = 5 * time.Second
	options.ReadTimeout = 10 * time.Second
	options.ContextTimeoutEnabled = true
	client := redis.NewClient(options)
	defer client.Close()
	queue, err := newB0ColdQueue(client, identity)
	if err != nil {
		return err
	}
	phase, err := b0ProducerMarkerPhase("/run/jobseek-lightpanda-producer", 10001, identity)
	if err != nil {
		return err
	}
	if command == "settle-rollback" {
		report, err := settleB0ColdRollback(ctx, queue, state, phase)
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"operation": command, "cohort": *cohort, "inflight_settled": report.InflightSettled, "expired_requeued": report.ExpiredRequeued, "dead_preserved": report.DeadPreserved})
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "lightpanda-b0-activation"
	}
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errB0Cutover
	}
	config, err := providerCutoverPoolConfig(dsn, role)
	if err != nil {
		return errB0Cutover
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errB0Cutover
	}
	defer pool.Close()
	if clearB0ColdRollback(ctx, pool, queue, phase, allowAbsent) != nil {
		return errB0Cutover
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"operation": command, "cohort": *cohort, "write_fences_remaining": 0, "digest": plan})
}
