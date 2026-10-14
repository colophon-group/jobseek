package main

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"golang.org/x/sys/unix"
)

func b0ColdFixtureIdentity() b0CutoverIdentity {
	return b0CutoverIdentity{Namespace: "cold-fixture", Shard: "lightpanda-b0", Cohort: "c1", Epoch: 7, SourceReceipt: strings.Repeat("a", 64), PlanDigest: strings.Repeat("b", 64)}
}

func TestRealB0ColdRollbackClearCanonicalFenceAndCompiledCommand(t *testing.T) {
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required private fixture absent")
		}
		t.Skip("requires private migrated fixture")
	}
	u, err := url.Parse(dsn)
	if err != nil || !(u.Hostname() == "127.0.0.1" || u.Hostname() == "localhost") || !strings.HasSuffix(u.Path, "_ordinary_worker_test") {
		t.Fatal("requires private canonical fixture")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	observer, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatal("private observer unavailable")
	}
	t.Cleanup(observer.Close)
	schema := fmt.Sprintf("b0_cold_%d", time.Now().UnixNano())
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err = observer.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal("private schema unavailable")
	}
	t.Cleanup(func() { _, _ = observer.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	if _, err = observer.Exec(ctx, "CREATE TABLE "+quoted+".lightpanda_b0_write_fence(engine_owner text,shard_id text,routing_epoch bigint)"); err != nil {
		t.Fatal("private fence relation unavailable")
	}
	params := u.Query()
	params.Set("search_path", schema)
	u.RawQuery = params.Encode()
	config, err := providerCutoverPoolConfig(u.String(), "lightpanda-b0-activation")
	if err != nil {
		t.Fatal("private pool configuration failed")
	}
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		t.Fatal("private canonical connection unavailable")
	}
	defer pool.Close()
	client := capacityRedisFixture(t, ctx)
	identity := b0ColdFixtureIdentity()
	q, err := newB0ColdQueue(client, identity)
	if err != nil {
		t.Fatal(err)
	}
	tombstone := map[string]any{"schema": b0RollbackSchema, "namespace": identity.Namespace, "shard_id": identity.Shard, "routing_epoch": "7", "engine_owner": "go", "cohort": identity.Cohort, "source_receipt_sha256": identity.SourceReceipt, "rollback_plan_digest": identity.PlanDigest}
	if client.HSet(ctx, b0OwnerKey, tombstone).Err() != nil {
		t.Fatal("private tombstone unavailable")
	}
	if _, err = pool.Exec(ctx, "INSERT INTO lightpanda_b0_write_fence VALUES('go','lightpanda-b0',7),('go','lightpanda-b0',8),('python','lightpanda-b0',7)"); err != nil {
		t.Fatal("private scope rows unavailable")
	}
	before, err := capacityKeySnapshot(ctx, client)
	if err != nil {
		t.Fatal(err)
	}
	if clearB0ColdRollback(ctx, pool, q, "absent", true) == nil {
		t.Fatal("canonical Go fence did not block cleanup")
	}
	after, err := capacityKeySnapshot(ctx, client)
	if err != nil || !reflect.DeepEqual(before, after) {
		t.Fatal("rejected cleanup changed exact tombstone")
	}
	if _, err = pool.Exec(ctx, "DELETE FROM lightpanda_b0_write_fence WHERE engine_owner='go' AND routing_epoch=7"); err != nil {
		t.Fatal("private scope fixture update failed")
	}
	if clearB0ColdRollback(ctx, pool, q, "active", true) == nil {
		t.Fatal("active producer sentinel allowed cleanup")
	}
	if err = clearB0ColdRollback(ctx, pool, q, "absent", false); err != nil {
		t.Fatal("exact cleanup failed", err)
	}
	var count int
	if pool.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence").Scan(&count) != nil || count != 2 {
		t.Fatal("cleanup changed unrelated canonical fences")
	}
	if clearB0ColdRollback(ctx, pool, q, "absent", false) == nil {
		t.Fatal("absent tombstone accepted without explicit recovery flag")
	}
	if clearB0ColdRollback(ctx, pool, q, "absent", true) != nil {
		t.Fatal("explicit idempotent recovery failed")
	}
	binary := filepath.Join(t.TempDir(), "go-typesense-exporter")
	if _, err = exec.CommandContext(ctx, "go", "build", "-o", binary, ".").CombinedOutput(); err != nil {
		t.Fatal("compiled native command unavailable")
	}
	command := exec.CommandContext(ctx, binary, "--b0-cold-rollback", "clear-rollback-tombstone", "--cohort", "c1", "--rollback-plan-digest", identity.PlanDigest, "--source-receipt-sha256", identity.SourceReceipt, "--allow-absent")
	command.Env = []string{"LOCAL_DATABASE_URL=" + u.String(), "REDIS_URL=unix://" + client.Options().Addr, "LIGHTPANDA_B0_PRODUCER_MODE=off", "LIGHTPANDA_B0_QUEUE_NAMESPACE=" + identity.Namespace, "LIGHTPANDA_B0_SHARD_ID=" + identity.Shard, "LIGHTPANDA_B0_ROUTING_EPOCH=7"}
	body, err := command.CombinedOutput()
	var reply map[string]any
	if err != nil || json.Unmarshal(body, &reply) != nil || reply["operation"] != "clear-rollback-tombstone" || reply["digest"] != identity.PlanDigest || reply["write_fences_remaining"] != float64(0) {
		t.Fatal("compiled cleanup ABI differs")
	}
	command = exec.CommandContext(ctx, binary, "--b0-cold-rollback", "settle-rollback", "--cohort", "c1", "--receipt-state", "pending", "--source-receipt-sha256", identity.SourceReceipt)
	command.Env = []string{"REDIS_URL=unix://" + client.Options().Addr, "LIGHTPANDA_B0_PRODUCER_MODE=off", "LIGHTPANDA_B0_QUEUE_NAMESPACE=" + identity.Namespace, "LIGHTPANDA_B0_SHARD_ID=" + identity.Shard, "LIGHTPANDA_B0_ROUTING_EPOCH=7"}
	body, err = command.CombinedOutput()
	if err != nil || json.Unmarshal(body, &reply) != nil || reply["operation"] != "settle-rollback" || reply["inflight_settled"] != float64(0) {
		t.Fatal("compiled pending-absence settle ABI differs")
	}
}

func b0ColdPrivateCall(t *testing.T, ctx context.Context, q *b0ColdQueue, operation string, change map[int]any) []any {
	t.Helper()
	i := q.identity
	args := []any{operation, i.Shard, strconv.FormatInt(i.Epoch, 10), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "0", "", "0", i.Namespace, "", "0", i.Cohort, "1", "0", "browser-use-careers"}
	for index, value := range change {
		args[index] = value
	}
	raw, err := q.script.Run(ctx, q.client, q.keys, args...).Result()
	values, ok := raw.([]any)
	if err != nil || !ok || len(values) != 12 || values[0] != "accepted" {
		t.Fatal("original private lifecycle refused", operation, raw, err)
	}
	return values
}

func TestB0ColdRollbackRealRedisExpiryAndLiveLeaseBounds(t *testing.T) {
	for _, mode := range []string{"expired-ready", "expired-dead", "live", "outside-window"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			client := capacityRedisFixture(t, ctx)
			q, err := newB0ColdQueue(client, b0ColdFixtureIdentity())
			if err != nil {
				t.Fatal(err)
			}
			b0ColdPrivateCall(t, ctx, q, "initialize_producer", nil)
			body, err := os.ReadFile("testdata/python_b0_cold_rollback.json")
			var corpus struct {
				Payload string `json:"task_payload"`
			}
			if err != nil || json.Unmarshal(body, &corpus) != nil {
				t.Fatal("actual original envelope absent")
			}
			var envelope map[string]any
			if json.Unmarshal([]byte(corpus.Payload), &envelope) != nil {
				t.Fatal("original envelope invalid")
			}
			payload := []byte(corpus.Payload)
			sha256Digest := sha256.Sum256(payload)
			sha1Digest := sha1.Sum(payload)
			config := map[string]string{"board_id": envelope["board_id"].(string), "domain": envelope["domain"].(string), "source_url": envelope["source_url"].(string), "description_r2_hash": "", "scrape_step": "0", "scrape_interval_hours": "24"}
			id := envelope["task_id"].(string)
			domain := config["domain"]
			if client.HSet(ctx, "scrape:"+id, config).Err() != nil || client.ZAdd(ctx, "ft_scrapes_browser:"+domain, redis.Z{Score: 0, Member: id}).Err() != nil {
				t.Fatal("original legacy fixture unavailable")
			}
			encoded, _ := json.Marshal(config)
			b0ColdPrivateCall(t, ctx, q, "activate_legacy", map[int]any{4: id, 5: "3", 10: corpus.Payload, 11: hex.EncodeToString(sha256Digest[:]), 12: hex.EncodeToString(sha1Digest[:]), 18: string(encoded), 19: "1", 22: "1"})
			if mode == "expired-dead" {
				raw, err := client.HGet(ctx, q.keys[1], id).Result()
				var record map[string]any
				if err != nil || json.Unmarshal([]byte(raw), &record) != nil {
					t.Fatal("private record absent")
				}
				record["failures"] = 2
				encoded, _ := json.Marshal(record)
				if client.HSet(ctx, q.keys[1], id, encoded).Err() != nil {
					t.Fatal("private failure fixture unavailable")
				}
			}
			ttl := "40"
			if mode == "live" {
				ttl = "120"
			}
			if mode == "outside-window" {
				ttl = "76000"
			}
			b0ColdPrivateCall(t, ctx, q, "claim_next", map[int]any{7: ttl})
			if strings.HasPrefix(mode, "expired-") {
				time.Sleep(60 * time.Millisecond)
			}
			before, err := capacityKeySnapshot(ctx, client)
			if err != nil {
				t.Fatal(err)
			}
			start := time.Now()
			report, err := settleB0ColdRollback(ctx, q, "active", "active")
			if mode == "outside-window" {
				after, snapshotErr := capacityKeySnapshot(ctx, client)
				if err == nil || snapshotErr != nil || !reflect.DeepEqual(before, after) {
					t.Fatal("over-budget live lease mutated or admitted")
				}
				return
			}
			want := b0SettleReport{InflightSettled: 1, ExpiredRequeued: 1}
			if mode == "expired-dead" {
				want.ExpiredRequeued = 0
				want.DeadPreserved = 1
			}
			if err != nil || report != want {
				t.Fatal("original expiry/requeue/dead conservation changed", report, err)
			}
			if mode == "live" && time.Since(start) < 75*time.Millisecond {
				t.Fatal("native command cleared live authority before expiry")
			}
			if inflight, err := client.ZCard(ctx, q.keys[3]).Result(); err != nil || inflight != 0 {
				t.Fatal("settled inflight authority remains")
			}
		})
	}
}

func TestB0ColdMarkerMatchesActualOriginalCorpus(t *testing.T) {
	body, err := os.ReadFile("testdata/python_b0_cold_rollback.json")
	var corpus struct {
		Cases []struct {
			Name, Phase string
			Payload     string `json:"payload_hex"`
		} `json:"marker_cases"`
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 35 {
		t.Fatal("original marker corpus unavailable")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			wire, err := hex.DecodeString(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			if got := b0ProducerMarkerPayloadPhase(wire, b0ColdFixtureIdentity()); got != c.Phase {
				t.Fatal("original marker phase differs", got, c.Phase)
			}
		})
	}
	for _, state := range []string{"active", "pending", "foreign"} {
		for _, phase := range []string{"absent", "partial", "preparing", "active", "unsafe"} {
			for _, present := range []bool{false, true} {
				want := present && (state == "active" && phase == "active" || state == "pending" && (phase == "active" || phase == "preparing")) || !present && state == "pending" && (phase == "absent" || phase == "partial" || phase == "preparing")
				if b0RollbackMarkerValid(state, phase, present) != want {
					t.Fatal("original receipt/marker gate differs")
				}
			}
		}
	}
}

func TestB0ColdMarkerDescriptorMetadataAndBoundedPipe(t *testing.T) {
	root := t.TempDir()
	directory := filepath.Join(root, "producer")
	identity := b0ColdFixtureIdentity()
	uid := uint32(os.Getuid())
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "absent" {
		t.Fatal("absent directory differs")
	}
	if os.Mkdir(directory, 0700) != nil {
		t.Fatal("private directory unavailable")
	}
	f := filepath.Join(directory, ".activation-v1")
	if os.WriteFile(f, []byte("P"), 0600) != nil {
		t.Fatal("private marker unavailable")
	}
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "partial" {
		t.Fatal("partial sentinel differs")
	}
	if os.Chmod(f, 0644) != nil {
		t.Fatal("fixture chmod failed")
	}
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "unsafe" {
		t.Fatal("readable marker accepted")
	}
	if os.Chmod(f, 0600) != nil || os.Link(f, f+"-link") != nil {
		t.Fatal("private hardlink unavailable")
	}
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "unsafe" {
		t.Fatal("multiply linked marker accepted")
	}
	if os.Remove(f) != nil || os.Symlink(f+"-link", f) != nil {
		t.Fatal("private symlink unavailable")
	}
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "unsafe" {
		t.Fatal("symlink marker accepted")
	}
	if os.Remove(f) != nil || unix.Mkfifo(f, 0600) != nil {
		t.Fatal("private FIFO unavailable")
	}
	start := time.Now()
	if phase, err := b0ProducerMarkerPhase(directory, uid, identity); err != nil || phase != "unsafe" || time.Since(start) > time.Second {
		t.Fatal("non-file marker blocked cold administration")
	}
}

func TestB0ColdQueueUsesUnchangedLuaAndRejectsMalformedReplies(t *testing.T) {
	body, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil || string(body) != b0CutoverQueueLua {
		t.Fatal("original fenced lifecycle changed")
	}
	valid := []any{"accepted", "audit_ok", "1000", "", "", "", "", "", "", "", "2", "1"}
	if result, err := decodeB0ColdTransition("audit", valid); err != nil || result != (b0ColdTransition{1000, 2, 1}) {
		t.Fatal("original audit reply refused", err)
	}
	for _, change := range []struct {
		Index int
		Value any
	}{{0, "not_current"}, {1, "reaped"}, {2, "0"}, {3, "foreign"}, {10, "2049"}, {11, "3"}, {2, 1000}, {10, "-1"}, {10, "2.0"}} {
		bad := append([]any{}, valid...)
		bad[change.Index] = change.Value
		if _, err := decodeB0ColdTransition("audit", bad); err == nil {
			t.Fatal("malformed audit admitted", change.Index)
		}
	}
	if _, err := decodeB0ColdTransition("audit", valid[:11]); err == nil {
		t.Fatal("short wire reply admitted")
	}
	reap := append([]any{}, valid...)
	reap[1], reap[10], reap[11] = "reaped", "64", "1"
	if _, err := decodeB0ColdTransition("reap_expired", reap); err == nil {
		t.Fatal("reaper exceeded original64-record limit")
	}
	clear := append([]any{}, valid...)
	clear[1], clear[10], clear[11] = "rollback_tombstone_cleared", "", ""
	if _, err := decodeB0ColdTransition("clear_rollback_tombstone", clear); err != nil {
		t.Fatal("original clear reply refused")
	}
	clear[10] = "0"
	if _, err := decodeB0ColdTransition("clear_rollback_tombstone", clear); err == nil {
		t.Fatal("cleanup accepted unexpected counter")
	}
}

func TestB0ColdRollbackRealRedisPendingAbsenceAndExactTombstone(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	client := capacityRedisFixture(t, ctx)
	identity := b0ColdFixtureIdentity()
	q, err := newB0ColdQueue(client, identity)
	if err != nil {
		t.Fatal(err)
	}
	for _, state := range []string{"active", "pending"} {
		for _, phase := range []string{"absent", "partial", "preparing", "active", "unsafe"} {
			before, err := capacityKeySnapshot(ctx, client)
			if err != nil {
				t.Fatal(err)
			}
			_, err = settleB0ColdRollback(ctx, q, state, phase)
			want := b0RollbackMarkerValid(state, phase, false)
			if (err == nil) != want {
				t.Fatal("cold absence receipt gate changed", state, phase, err)
			}
			after, err := capacityKeySnapshot(ctx, client)
			if err != nil || !reflect.DeepEqual(before, after) {
				t.Fatal("empty settle changed Redis authority")
			}
		}
	}
	tombstone := map[string]any{"schema": b0RollbackSchema, "namespace": identity.Namespace, "shard_id": identity.Shard, "routing_epoch": "7", "engine_owner": "go", "cohort": identity.Cohort, "source_receipt_sha256": identity.SourceReceipt, "rollback_plan_digest": identity.PlanDigest}
	if client.HSet(ctx, b0OwnerKey, tombstone).Err() != nil {
		t.Fatal("private tombstone unavailable")
	}
	if present, err := q.boundTombstone(ctx, identity.PlanDigest); err != nil || !present {
		t.Fatal("exact retained tombstone refused", err)
	}
	if _, err := settleB0ColdRollback(ctx, q, "active", "absent"); err != nil {
		t.Fatal("committed recovery refused", err)
	}
	if _, err := q.boundTombstone(ctx, strings.Repeat("c", 64)); err == nil {
		t.Fatal("stale plan digest accepted")
	}
	if client.Set(ctx, q.keys[0], "foreign", 0).Err() != nil {
		t.Fatal("private foreign namespace unavailable")
	}
	if _, err := q.boundTombstone(ctx, identity.PlanDigest); err == nil {
		t.Fatal("tombstone coexisting with authority accepted")
	}
}
