//go:build integration

package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func coldCleanupRedisFixture(t *testing.T) (*b0Queue, *producerActivationSentinel, producerColdCleanupDecision, []byte, string) {
	t.Helper()
	client, q, owner := integrationRedisQueue(t)
	ctx := context.Background()
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	d := cleanupTestDecision()
	d.Namespace, d.SourceEpoch, d.RetirementEpoch = owner.Namespace, owner.Route.RoutingEpoch, owner.Route.RoutingEpoch+1
	directory := t.TempDir()
	if err := os.Chmod(directory, 0700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(filepath.Join(directory, ".activation-v1"), owner, uint32(os.Geteuid()))
	if err != nil || sentinel.ensurePreparing() != nil || sentinel.publishActive() != nil {
		t.Fatal("private cleanup sentinel", err)
	}
	marker, err := os.ReadFile(sentinel.path)
	if err != nil {
		t.Fatal(err)
	}
	d.ActivationSentinelSHA256 = cleanupTestDigest(marker)
	tombstone := map[string]string{"schema": "jobseek.lightpanda.producer-rollback/v1", "namespace": d.Namespace, "shard_id": d.ShardID, "routing_epoch": strconv.FormatInt(d.SourceEpoch, 10), "engine_owner": engineOwner, "cohort": d.Cohort, "rollback_plan_digest": d.B0RestorationPlanSHA256, "source_receipt_sha256": d.SourceReceiptSHA256}
	if client.HSet(ctx, producerOwnerKey, tombstone).Err() != nil || client.Set(ctx, "cleanup-unrelated", "1925089445.100001", 0).Err() != nil || client.ZAdd(ctx, "ready:browser:0", redis.Z{Member: "unrelated.example.test", Score: 350.0001}).Err() != nil {
		t.Fatal("private cleanup Redis seed")
	}
	body, _ := json.Marshal(d)
	return q, sentinel, d, body, filepath.Join(directory, "history")
}

func TestColdProducerCleanupRealRedisSaveFailureAndExactRecovery(t *testing.T) {
	q, sentinel, d, body, prefix := coldCleanupRedisFixture(t)
	ctx := context.Background()
	saves := 0
	save := func(ctx context.Context) error {
		saves++
		result, err := q.client.Save(ctx).Result()
		if err != nil || result != "OK" {
			return errors.New("SAVE denied")
		}
		return nil
	}
	if err := q.client.Do(ctx, "ACL", "SETUSER", "default", "-save").Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = q.client.Do(ctx, "ACL", "SETUSER", "default", "+save").Err() })
	if cleanupColdProducer(ctx, q, sentinel, d, body, prefix, save) == nil {
		t.Fatal("denied SAVE granted cleanup completion")
	}
	if _, err := os.Lstat(prefix + ".complete"); !os.IsNotExist(err) || q.client.Exists(ctx, producerOwnerKey).Val() != 0 {
		t.Fatal("returned Lua effect/completion boundary not exercised")
	}
	request, err := readProducerColdFile(prefix+".request", uint32(os.Geteuid()), false)
	if err != nil || string(request) != string(body) {
		t.Fatal("decision was not durable before sentinel/Lua effects")
	}
	if err := q.client.Do(ctx, "ACL", "SETUSER", "default", "+save").Err(); err != nil {
		t.Fatal(err)
	}
	if err := cleanupColdProducer(ctx, q, sentinel, d, body, prefix, save); err != nil {
		t.Fatal("exact pending effect recovery", err)
	}
	if err := cleanupColdProducer(ctx, q, sentinel, d, body, prefix, save); err != nil || saves != 2 {
		t.Fatal("completed cleanup repeated SAVE", err, saves)
	}
	for _, suffix := range []string{".request", ".complete"} {
		retained, err := readProducerColdFile(prefix+suffix, uint32(os.Geteuid()), false)
		if err != nil || string(retained) != string(body) {
			t.Fatal("cleanup history changed", err)
		}
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelAbsent || q.client.Get(ctx, "cleanup-unrelated").Val() != "1925089445.100001" || q.client.ZScore(ctx, "ready:browser:0", "unrelated.example.test").Val() != 350.0001 {
		t.Fatal("cleanup source retirement/unrelated values")
	}
	if err := os.Remove(prefix + ".request"); err != nil {
		t.Fatal(err)
	}
	if cleanupColdProducer(ctx, q, sentinel, d, body, prefix, save) == nil || saves != 2 {
		t.Fatal("missing cleanup history reconstructed from absence")
	}
}

func TestColdProducerCleanupRejectsForeignSourceWithoutEffects(t *testing.T) {
	for _, drift := range []string{"owner", "source_receipt", "extra_owner", "expiring_owner", "source_queue", "legacy_guard", "sentinel", "absent_owner", "cancelled"} {
		t.Run(drift, func(t *testing.T) {
			q, sentinel, d, body, prefix := coldCleanupRedisFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			switch drift {
			case "owner":
				q.client.HSet(ctx, producerOwnerKey, "routing_epoch", "8")
			case "source_receipt":
				q.client.HSet(ctx, producerOwnerKey, "source_receipt_sha256", "foreign")
			case "extra_owner":
				q.client.HSet(ctx, producerOwnerKey, "extra", "field")
			case "expiring_owner":
				if err := q.client.PExpire(ctx, producerOwnerKey, 10*time.Minute).Err(); err != nil {
					t.Fatal(err)
				}
			case "source_queue":
				q.client.Set(ctx, q.keys[1], "orphan", 0)
			case "legacy_guard":
				q.client.HSet(ctx, legacyGuardKey, "foreign", "guard")
			case "sentinel":
				if err := sentinel.clearForRollback(); err != nil {
					t.Fatal(err)
				}
			case "absent_owner":
				q.client.Del(ctx, producerOwnerKey)
			case "cancelled":
				cancel()
			}
			before, err := q.client.HGetAll(context.Background(), producerOwnerKey).Result()
			if err != nil {
				t.Fatal(err)
			}
			marker, markerErr := os.ReadFile(sentinel.path)
			if cleanupColdProducer(ctx, q, sentinel, d, body, prefix, func(context.Context) error { t.Fatal("rejected cleanup saved"); return nil }) == nil {
				t.Fatal("foreign or incomplete source accepted")
			}
			after, err := q.client.HGetAll(context.Background(), producerOwnerKey).Result()
			current, currentErr := os.ReadFile(sentinel.path)
			if err != nil || !reflect.DeepEqual(before, after) || string(marker) != string(current) || (markerErr == nil) != (currentErr == nil) {
				t.Fatal("rejected cleanup mutated authority")
			}
			if _, err := os.Lstat(prefix + ".request"); !os.IsNotExist(err) {
				t.Fatal("rejected source retained cleanup request")
			}
		})
	}
}

func TestColdProducerCleanupRecoversSentinelOnlyEffectAndRefusesChangedHistory(t *testing.T) {
	q, sentinel, d, body, prefix := coldCleanupRedisFixture(t)
	ctx := context.Background()
	if err := retainProducerColdFile(prefix+".request", body); err != nil || sentinel.clearForRollback() != nil {
		t.Fatal("private sentinel-only interruption", err)
	}
	changed := append(append([]byte{}, body...), '\n')
	if cleanupColdProducer(ctx, q, sentinel, d, changed, prefix, func(context.Context) error { t.Fatal("changed request saved"); return nil }) == nil {
		t.Fatal("pending cleanup request replaced")
	}
	if err := cleanupColdProducer(ctx, q, sentinel, d, body, prefix, func(ctx context.Context) error { return q.client.Save(ctx).Err() }); err != nil {
		t.Fatal("sentinel-only pending cleanup did not recover", err)
	}
	if err := q.client.HSet(ctx, producerOwnerKey, "foreign", "authority").Err(); err != nil {
		t.Fatal(err)
	}
	if cleanupColdProducer(ctx, q, sentinel, d, body, prefix, func(context.Context) error { t.Fatal("completed history repaired"); return nil }) == nil {
		t.Fatal("completed cleanup silently repaired a new owner")
	}
}
