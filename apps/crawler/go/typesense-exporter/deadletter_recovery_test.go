package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"reflect"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func TestDeadletterRecoveryArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"bad"}, {"inspect", "--apply"}, {"retry"}, {"prune", "--entry", "x", "extra"}, {"prune", "--unknown"}} {
		if _, err := parseDeadletterOptions(args); err == nil {
			t.Errorf("accepted invalid arguments %v", args)
		}
	}
	option, err := parseDeadletterOptions([]string{"retry", "--entry", "a", "--entry", "b", "--apply"})
	if err != nil || !reflect.DeepEqual(option, deadletterOptions{Action: "retry", Refs: []string{"a", "b"}, Apply: true}) {
		t.Fatalf("parse: %+v %v", option, err)
	}
	input, _ := deadletterOracle(t)
	report, err := classifyDeadletterSnapshot(input)
	if err != nil {
		t.Fatal(err)
	}
	var active, retired, noncanonical string
	for _, entry := range report.Entries {
		if entry.Retryable {
			active = entry.Ref
		}
		if entry.Reason == "removed" {
			if strings.Contains(entry.TaskID, "ABCDEF") {
				noncanonical = entry.Ref
			} else if len(entry.TaskID) == 36 {
				retired = entry.Ref
			}
		}
	}
	for _, option := range []deadletterOptions{{Action: "prune", Refs: []string{active}}, {Action: "retry", Refs: []string{retired}}, {Action: "prune", Refs: []string{noncanonical}}, {Action: "retry", Refs: []string{"missing"}}} {
		if _, err := selectDeadletters(report, option); err == nil {
			t.Errorf("accepted unsafe selection %+v", option)
		}
	}
	selected, err := selectDeadletters(report, deadletterOptions{Action: "retry", Refs: []string{active, active}})
	if err != nil || len(selected.Entries) != 1 {
		t.Fatalf("exact selector deduplication: %+v %v", selected, err)
	}
	d := deadletterDelays{Default: 2, ATS: 0.5}
	for domain, want := range map[string]float64{"greenhouse": 0.5, "example.avature.net": 0.5, "example.test": 2} {
		if got := d.forDomain(domain); got != want {
			t.Errorf("delay %s: %v", domain, got)
		}
	}
}

type deadletterEvalHook struct{ before func() }

func (h deadletterEvalHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) { return next(ctx, network, addr) }
}
func (h deadletterEvalHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "eval" {
			h.before()
		}
		return next(ctx, cmd)
	}
}
func (h deadletterEvalHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}

// Invoked inside the isolated PostgreSQL/Redis test after read-only checks.
func exerciseDeadletterRecovery(t *testing.T, ctx context.Context, conn *pgx.Conn, dsn, schema string, admin *redis.Client, input deadletterSnapshot) {
	t.Helper()
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	config.MaxConns = 1
	config.ConnConfig.RuntimeParams["search_path"] = schema
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	delays := deadletterDelays{Default: 2, ATS: 0.5}
	seed := func(index int) (deadletterSnapshot, deadletterEntry) {
		t.Helper()
		if err := admin.FlushDB(ctx).Err(); err != nil {
			t.Fatal(err)
		}
		id := fmt.Sprintf("abcdef00-0000-0000-0000-%012d", index)
		var raw deadletterEntry
		found := false
		for _, candidate := range input.Entries {
			if strings.HasSuffix(candidate.Member, id) {
				raw = candidate
				found = true
				break
			}
		}
		if !found {
			t.Fatal("missing recovery fixture")
		}
		if err := admin.ZAdd(ctx, "deadletter:"+raw.Wtype, redis.Z{Member: raw.Member, Score: raw.ReapedAt}).Err(); err != nil {
			t.Fatal(err)
		}
		if values := input.Configs[id]; len(values) > 0 {
			if err := admin.HSet(ctx, "board:"+id, values).Err(); err != nil {
				t.Fatal(err)
			}
		}
		snapshot, err := readDeadletterSnapshot(ctx, pool, admin)
		if err != nil {
			t.Fatal(err)
		}
		report, err := classifyDeadletterSnapshot(snapshot)
		if err != nil || len(report.Entries) != 1 {
			t.Fatalf("seed report: %+v %v", report, err)
		}
		return snapshot, report.Entries[0]
	}
	assertParked := func(entry deadletterEntry) {
		t.Helper()
		score, err := admin.ZScore(ctx, "deadletter:"+entry.Wtype, entry.Member).Result()
		if err != nil || score != entry.ReapedAt {
			t.Fatal("unsafe recovery changed poison descriptor")
		}
	}
	_, entry := seed(1)
	option := deadletterOptions{Action: "retry", Refs: []string{entry.Ref}}
	result, err := resolveDeadletters(ctx, pool, admin, option, delays)
	if err != nil || result.Outcomes[0]["outcome"] != "would_retry" {
		t.Fatalf("dry run: %+v %v", result, err)
	}
	assertParked(entry)
	snapshot, entry := seed(1)
	option.Apply = true
	result, err = resolveDeadletters(ctx, pool, admin, option, delays)
	if err != nil || result.Outcomes[0]["schedule"] != "enqueued" {
		t.Fatalf("retry: %+v %v", result, err)
	}
	queue := "monitors_simple:example.test"
	before, err := admin.ZScore(ctx, queue, entry.TaskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	if admin.Get(ctx, "delay:example.test").Val() != "2.0" || admin.ZCard(ctx, "deadletter:simple").Val() != 0 {
		t.Fatal("retry did not establish schedule and delay")
	}
	if _, err := recoverDeadletter(ctx, pool, admin, snapshot, entry, "retry", delays); err == nil {
		t.Fatal("replayed mutation was acknowledged")
	}
	if after := admin.ZScore(ctx, queue, entry.TaskID).Val(); after != before {
		t.Fatal("replay changed due time")
	}
	for _, kind := range []string{"first", "recurring", "inflight"} {
		_, entry = seed(1)
		key, member := "ft_monitors_simple:example.test", entry.TaskID
		if kind == "recurring" {
			key = queue
		}
		if kind == "inflight" {
			key, member = "inflight:simple", entry.Member
		}
		const due = 2000000000.125
		if err := admin.ZAdd(ctx, key, redis.Z{Member: member, Score: due}).Err(); err != nil {
			t.Fatal(err)
		}
		result, err = resolveDeadletters(ctx, pool, admin, option, delays)
		if err != nil || result.Outcomes[0]["schedule"] != "already_scheduled" {
			t.Fatalf("existing %s: %+v %v", kind, result, err)
		}
		if admin.ZScore(ctx, key, member).Val() != due {
			t.Fatal("existing due time changed")
		}
		if kind != "recurring" && admin.ZCard(ctx, queue).Val() != 0 {
			t.Fatal("duplicate schedule created")
		}
		if admin.Exists(ctx, "delay:example.test").Val() != 0 {
			t.Fatal("existing schedule's delay changed")
		}
	}
	for _, index := range []int{2, 3, 4} {
		_, entry = seed(index)
		result, err = resolveDeadletters(ctx, pool, admin, deadletterOptions{Action: "prune", Refs: []string{entry.Ref}, Apply: true}, delays)
		if err != nil || result.Outcomes[0]["schedule"] != "not_applicable" || admin.ZCard(ctx, "deadletter:simple").Val() != 0 {
			t.Fatalf("retired prune: %+v %v", result, err)
		}
	}
	_, entry = seed(7)
	if err := admin.ZAdd(ctx, "monitors_simple:old.example.test", redis.Z{Member: entry.TaskID, Score: 2000000000}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := admin.HSet(ctx, "inflight_strikes:simple", entry.Member, "3").Err(); err != nil {
		t.Fatal(err)
	}
	result, err = resolveDeadletters(ctx, pool, admin, deadletterOptions{Action: "prune", Refs: []string{entry.Ref}, Apply: true}, delays)
	if err != nil || result.Outcomes[0]["schedule"] != "enqueued" || admin.ZCard(ctx, "monitors_simple:old.example.test").Val() != 0 || admin.HExists(ctx, "inflight_strikes:simple", entry.Member).Val() {
		t.Fatalf("superseded prune: %+v %v", result, err)
	}
	// The obsolete inflight owner must stop the entire transition before a new
	// schedule appears, preserving both current config and parked evidence.
	_, entry = seed(7)
	if err := admin.ZAdd(ctx, "inflight:simple", redis.Z{Member: entry.Member, Score: 2000000000}).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := resolveDeadletters(ctx, pool, admin, deadletterOptions{Action: "prune", Refs: []string{entry.Ref}, Apply: true}, delays); err == nil {
		t.Fatal("superseded inflight accepted")
	}
	assertParked(entry)
	if admin.ZCard(ctx, queue).Val() != 0 {
		t.Fatal("unsafe current schedule created")
	}
	snapshot, entry = seed(1)
	if _, err := conn.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1", entry.TaskID); err != nil {
		t.Fatal(err)
	}
	if _, err := recoverDeadletter(ctx, pool, admin, snapshot, entry, "retry", delays); err == nil {
		t.Fatal("changed PostgreSQL authority accepted")
	}
	assertParked(entry)
	if _, err := conn.Exec(ctx, "UPDATE job_board SET is_enabled=true WHERE id=$1", entry.TaskID); err != nil {
		t.Fatal(err)
	}
	for _, change := range []string{"config", "score", "ready_type"} {
		snapshot, entry = seed(1)
		switch change {
		case "config":
			err = admin.HSet(ctx, "board:"+entry.TaskID, "new_field", "changed").Err()
		case "score":
			err = admin.ZAdd(ctx, "deadletter:simple", redis.Z{Member: entry.Member, Score: entry.ReapedAt + 1}).Err()
		case "ready_type":
			err = admin.Set(ctx, "ready:simple:1", "corrupt", 0).Err()
		}
		if err != nil {
			t.Fatal(err)
		}
		if _, err := mutateDeadletter(ctx, admin, entry, snapshot.Configs[entry.TaskID], "retry", delays); err == nil {
			t.Fatalf("accepted atomic %s drift", change)
		}
		if admin.ZCard(ctx, "deadletter:simple").Val() != 1 || admin.ZCard(ctx, queue).Val() != 0 {
			t.Fatal("failed guard changed schedule or descriptor")
		}
	}
	// Prove the board row lock is still held at the Redis mutation boundary.
	snapshot, entry = seed(1)
	hookOptions := *admin.Options()
	hookOptions.MaxRetries = -1
	hooked := redis.NewClient(&hookOptions)
	defer hooked.Close()
	observed := false
	hooked.AddHook(deadletterEvalHook{before: func() {
		observed = true
		if _, err := conn.Exec(ctx, "SET lock_timeout='100ms'"); err != nil {
			t.Fatal(err)
		}
		_, err := conn.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1", entry.TaskID)
		var postgresError *pgconn.PgError
		if !errors.As(err, &postgresError) || postgresError.Code != "55P03" {
			t.Fatalf("authority row was not locked: %v", err)
		}
	}})
	if _, err := recoverDeadletter(ctx, pool, hooked, snapshot, entry, "retry", delays); err != nil {
		t.Fatal(err)
	}
	if !observed {
		t.Fatal("Redis mutation did not execute")
	}
	if _, err := conn.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1", entry.TaskID); err != nil {
		t.Fatal("recovery did not release board lock")
	}
}
