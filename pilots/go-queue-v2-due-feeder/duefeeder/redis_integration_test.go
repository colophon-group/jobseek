package duefeeder

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
	"github.com/colophon-group/jobseek/pilots/go-queue-v2-admission/queueworker"
	"github.com/redis/go-redis/v9"
)

const isolatedDueFeederRedisURL = "redis://127.0.0.1:6382/15"

type redisKeySnapshot struct {
	Kind string
	Dump string
}

type rotatingSink struct {
	base   *queueworker.Runner
	rotate func() error
	once   sync.Once
	err    error
}

func (s *rotatingSink) Submit(ctx context.Context, candidate queueworker.Candidate) error {
	s.once.Do(func() { s.err = s.rotate() })
	if s.err != nil {
		return s.err
	}
	return s.base.Submit(ctx, candidate)
}

func (s *rotatingSink) Close() { s.base.Close() }

func TestRealRedisReadOnlySnapshotAndStaleRevisionFence(t *testing.T) {
	redisURL, urlSet := os.LookupEnv("QUEUE_V2_DUE_FEEDER_REDIS_URL")
	isolated, isolatedSet := os.LookupEnv("QUEUE_V2_DUE_FEEDER_REDIS_ISOLATED")
	if !urlSet && !isolatedSet {
		t.Skip("requires isolated queue-v2 due-feeder Redis opt-in")
	}
	if !urlSet || !isolatedSet || isolated != "1" || redisURL != isolatedDueFeederRedisURL {
		t.Fatal("due-feeder Redis test requires both opt-ins and exact isolated redis://127.0.0.1:6382/15")
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	if options.Addr != "127.0.0.1:6382" || options.DB != 15 ||
		options.Username != "" || options.Password != "" || options.TLSConfig != nil {
		t.Fatalf("unsafe Redis target: addr=%q db=%d", options.Addr, options.DB)
	}
	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		t.Fatal(err)
	}
	if size, err := client.DBSize(ctx).Result(); err != nil || size != 0 {
		_ = client.Close()
		t.Fatalf("isolated Redis DB 15 must start empty: size=%d err=%v", size, err)
	}

	_, sourceFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate integration test source")
	}
	luaPath := filepath.Join(
		filepath.Dir(sourceFile), "..", "..", "..",
		"apps", "crawler", "contracts", "queue", "v2", "redis", "lifecycle.lua",
	)
	luaSource, err := os.ReadFile(filepath.Clean(luaPath))
	if err != nil {
		t.Fatal(err)
	}
	namespace := fmt.Sprintf("due-feeder-%d", time.Now().UnixNano())
	queue, err := queuev2.NewRedisCandidateClient(client, namespace, string(luaSource))
	if err != nil {
		t.Fatal(err)
	}
	keys := queue.Keys()
	if len(keys) != 7 {
		t.Fatalf("candidate client exposed %d keys", len(keys))
	}
	var cleanupOnce sync.Once
	cleanup := func() {
		cleanupOnce.Do(func() {
			cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cleanupCancel()
			if err := client.Del(cleanupCtx, keys...).Err(); err != nil {
				t.Errorf("delete exact candidate keys: %v", err)
			}
			if size, err := client.DBSize(cleanupCtx).Result(); err != nil || size != 0 {
				t.Errorf("isolated Redis DB 15 did not end empty: size=%d err=%v", size, err)
			}
			if err := client.Close(); err != nil {
				t.Errorf("close Redis: %v", err)
			}
		})
	}
	t.Cleanup(cleanup)

	route := queuev2.Route{ShardID: "due-shard", RoutingEpoch: 29, EngineOwner: "go"}
	transition, err := queue.Execute(ctx, queuev2.RedisCandidateOperation{Kind: "initialize", Route: route})
	if err != nil || transition.Decision != queuev2.RedisAccepted {
		t.Fatalf("initialize: %+v err=%v", transition, err)
	}
	revisions := map[string]int64{"a": 1, "b": 2, "c": 3, "future": 4}
	for taskID, revision := range revisions {
		transition, err := queue.Execute(ctx, queuev2.RedisCandidateOperation{
			Kind: "register", TaskID: taskID, Route: route, ConfigRevision: revision,
		})
		if err != nil || transition.Decision != queuev2.RedisAccepted {
			t.Fatalf("register %s: %+v err=%v", taskID, transition, err)
		}
	}
	serverTime, err := client.Time(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	nowMS := serverTime.UnixMilli()
	if err := client.ZAdd(ctx, keys[3],
		redis.Z{Member: "a", Score: float64(nowMS - 2)},
		redis.Z{Member: "c", Score: float64(nowMS - 1)},
		redis.Z{Member: "b", Score: float64(nowMS - 1)},
		redis.Z{Member: "future", Score: float64(nowMS + 60_000)},
	).Err(); err != nil {
		t.Fatal(err)
	}
	source, err := NewRedisDueSource(client, queue)
	if err != nil {
		t.Fatal(err)
	}

	before := snapshotRedisKeys(t, ctx, client, keys)
	references, err := source.ListDue(ctx, 3)
	if err != nil {
		t.Fatal(err)
	}
	wantReferences := []Reference{
		{TaskID: "a", ConfigRevision: 1},
		{TaskID: "b", ConfigRevision: 2},
		{TaskID: "c", ConfigRevision: 3},
	}
	if !reflect.DeepEqual(references, wantReferences) {
		t.Fatalf("references=%+v want=%+v", references, wantReferences)
	}
	after := snapshotRedisKeys(t, ctx, client, keys)
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("read-only source changed queue keys\nbefore=%+v\nafter=%+v", before, after)
	}

	assertPoisonReadOnly(t, ctx, client, source, keys, func() error {
		return client.HSet(ctx, keys[1], "a", "01").Err()
	}, func() error {
		return client.HSet(ctx, keys[1], "a", "1").Err()
	})
	assertPoisonReadOnly(t, ctx, client, source, keys, func() error {
		return client.HSet(ctx, keys[1], "a", "10000000000000").Err()
	}, func() error {
		return client.HSet(ctx, keys[1], "a", "1").Err()
	})
	assertPoisonReadOnly(t, ctx, client, source, keys, func() error {
		return client.HDel(ctx, keys[1], "a").Err()
	}, func() error {
		return client.HSet(ctx, keys[1], "a", "1").Err()
	})
	assertPoisonReadOnly(t, ctx, client, source, keys, func() error {
		return client.ZAdd(ctx, keys[3], redis.Z{Member: "a", Score: float64(nowMS) - 0.5}).Err()
	}, func() error {
		return client.ZAdd(ctx, keys[3], redis.Z{Member: "a", Score: float64(nowMS - 2)}).Err()
	})
	oversized := strings.Repeat("x", maxTaskIDBytes+1)
	assertPoisonReadOnly(t, ctx, client, source, keys, func() error {
		pipe := client.Pipeline()
		pipe.ZAdd(ctx, keys[3], redis.Z{Member: oversized, Score: float64(nowMS - 3)})
		pipe.HSet(ctx, keys[1], oversized, "1")
		_, err := pipe.Exec(ctx)
		return err
	}, func() error {
		_, err := client.Pipelined(ctx, func(pipe redis.Pipeliner) error {
			pipe.ZRem(ctx, keys[3], oversized)
			pipe.HDel(ctx, keys[1], oversized)
			return nil
		})
		return err
	})

	// The reference is exactly revision 1 when resolved. Rotating the existing
	// config hash to revision 2 before Submit makes the unchanged claim CAS
	// reject it, and the executor never runs.
	var executions atomic.Int64
	runner, err := queueworker.New(queueworker.Config{
		Pool: worker.Config{
			Workers: 1, Capacity: 1, ResultCapacity: 1, PerOriginConcurrency: 1,
			JobTimeout: time.Second, ShutdownGrace: time.Second,
		},
		Route: route, LeaseTTLMS: 1_000,
		HeartbeatInterval: 100 * time.Millisecond,
		HeartbeatTimeout:  250 * time.Millisecond,
		TerminalTimeout:   500 * time.Millisecond,
	}, queue, queueworker.ExecutorFunc(func(
		context.Context, worker.Job, queuev2.LeaseFence,
	) (sitemap.Result, queueworker.Disposition, error) {
		executions.Add(1)
		return sitemap.Result{}, queueworker.Disposition{Kind: queueworker.DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	sink := &rotatingSink{
		base: runner,
		rotate: func() error {
			return client.HSet(ctx, keys[1], "a", "2").Err()
		},
	}
	resolver := CandidateResolverFunc(func(_ context.Context, reference Reference) (ResolvedCandidate, error) {
		return candidateFor(reference, "https://127.0.0.1:41001/sitemap.xml"), nil
	})
	feeder, err := New(source, resolver, sink)
	if err != nil {
		t.Fatal(err)
	}
	report, err := feeder.FeedOnce(ctx, 1)
	if err != nil || report != (Report{Observed: 1, Resolved: 1, Submitted: 1}) {
		t.Fatalf("stale feed report=%+v err=%v", report, err)
	}
	select {
	case result := <-runner.Results():
		if !errors.Is(result.Err, queueworker.ErrClaimNotAccepted) {
			t.Fatalf("stale result error=%v", result.Err)
		}
		var transitionError *queueworker.TransitionError
		if !errors.As(result.Err, &transitionError) ||
			transitionError.Operation != "claim" ||
			transitionError.Transition.Decision != queuev2.RedisFenced ||
			transitionError.Transition.Reason != "config_revision_mismatch" {
			t.Fatalf("stale claim transition=%+v error=%v", transitionError, result.Err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for stale claim rejection")
	}
	feeder.Close()
	select {
	case _, ok := <-runner.Results():
		if ok {
			t.Fatal("unexpected trailing runner result")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("runner did not close")
	}
	if executions.Load() != 0 {
		t.Fatalf("stale revision executions=%d", executions.Load())
	}
	cleanup()
}

func TestRealRedisFeedOnceKeepsBeyondWorkerCapacityUnclaimed(t *testing.T) {
	redisURL, urlSet := os.LookupEnv("QUEUE_V2_DUE_FEEDER_REDIS_URL")
	isolated, isolatedSet := os.LookupEnv("QUEUE_V2_DUE_FEEDER_REDIS_ISOLATED")
	if !urlSet && !isolatedSet {
		t.Skip("requires isolated queue-v2 due-feeder Redis opt-in")
	}
	if !urlSet || !isolatedSet || isolated != "1" || redisURL != isolatedDueFeederRedisURL {
		t.Fatal("due-feeder Redis test requires both opt-ins and exact isolated redis://127.0.0.1:6382/15")
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	if options.Addr != "127.0.0.1:6382" || options.DB != 15 ||
		options.Username != "" || options.Password != "" || options.TLSConfig != nil {
		t.Fatalf("unsafe Redis target: addr=%q db=%d", options.Addr, options.DB)
	}
	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		t.Fatal(err)
	}
	if size, err := client.DBSize(ctx).Result(); err != nil || size != 0 {
		_ = client.Close()
		t.Fatalf("isolated Redis DB 15 must start empty: size=%d err=%v", size, err)
	}

	luaSource := readLifecycleLua(t)
	namespace := fmt.Sprintf("due-feeder-concurrency-%d", time.Now().UnixNano())
	queue, err := queuev2.NewRedisCandidateClient(client, namespace, luaSource)
	if err != nil {
		t.Fatal(err)
	}
	keys := queue.Keys()
	var cleanupOnce sync.Once
	cleanup := func() {
		cleanupOnce.Do(func() {
			cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cleanupCancel()
			if err := client.Del(cleanupCtx, keys...).Err(); err != nil {
				t.Errorf("delete exact candidate keys: %v", err)
			}
			if size, err := client.DBSize(cleanupCtx).Result(); err != nil || size != 0 {
				t.Errorf("isolated Redis DB 15 did not end empty: size=%d err=%v", size, err)
			}
			if err := client.Close(); err != nil {
				t.Errorf("close Redis: %v", err)
			}
		})
	}
	t.Cleanup(cleanup)

	const (
		workers = 3
		tasks   = 6
	)
	route := queuev2.Route{ShardID: "due-concurrency", RoutingEpoch: 31, EngineOwner: "go"}
	transition, err := queue.Execute(ctx, queuev2.RedisCandidateOperation{Kind: "initialize", Route: route})
	if err != nil || transition.Decision != queuev2.RedisAccepted {
		t.Fatalf("initialize: %+v err=%v", transition, err)
	}
	for index := range tasks {
		taskID := fmt.Sprintf("task-%d", index)
		transition, err := queue.Execute(ctx, queuev2.RedisCandidateOperation{
			Kind: "register", TaskID: taskID, Route: route, ConfigRevision: 1,
		})
		if err != nil || transition.Decision != queuev2.RedisAccepted {
			t.Fatalf("register %s: %+v err=%v", taskID, transition, err)
		}
	}
	source, err := NewRedisDueSource(client, queue)
	if err != nil {
		t.Fatal(err)
	}
	started := make(chan string, tasks)
	release := make(chan struct{})
	var active atomic.Int64
	var maxActive atomic.Int64
	runner, err := queueworker.New(queueworker.Config{
		Pool: worker.Config{
			Workers: workers, Capacity: tasks, ResultCapacity: tasks, PerOriginConcurrency: 1,
			JobTimeout: 3 * time.Second, ShutdownGrace: 2 * time.Second,
		},
		Route: route, LeaseTTLMS: 2_000,
		HeartbeatInterval: 200 * time.Millisecond,
		HeartbeatTimeout:  500 * time.Millisecond,
		TerminalTimeout:   500 * time.Millisecond,
	}, queue, queueworker.ExecutorFunc(func(
		ctx context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, queueworker.Disposition, error) {
		current := active.Add(1)
		for observed := maxActive.Load(); current > observed; observed = maxActive.Load() {
			if maxActive.CompareAndSwap(observed, current) {
				break
			}
		}
		started <- job.ID
		select {
		case <-release:
		case <-ctx.Done():
			active.Add(-1)
			return sitemap.Result{}, queueworker.Disposition{Kind: queueworker.DispositionComplete}, ctx.Err()
		}
		active.Add(-1)
		return sitemap.Result{}, queueworker.Disposition{Kind: queueworker.DispositionComplete}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	resolver := CandidateResolverFunc(func(_ context.Context, reference Reference) (ResolvedCandidate, error) {
		var index int
		if _, err := fmt.Sscanf(reference.TaskID, "task-%d", &index); err != nil || index < 0 || index >= tasks {
			return ResolvedCandidate{}, errors.New("unexpected reference")
		}
		return candidateFor(reference, fmt.Sprintf("http://127.0.0.1:%d/sitemap.xml", 42000+index)), nil
	})
	feeder, err := New(source, resolver, runner)
	if err != nil {
		t.Fatal(err)
	}
	report, err := feeder.FeedOnce(ctx, tasks)
	if err != nil || report != (Report{Observed: tasks, Resolved: tasks, Submitted: tasks}) {
		t.Fatalf("feed report=%+v err=%v", report, err)
	}
	for range workers {
		select {
		case <-started:
		case <-time.After(2 * time.Second):
			t.Fatal("workers did not overlap")
		}
	}
	if maxActive.Load() != workers || active.Load() != workers {
		t.Fatalf("active=%d max_active=%d", active.Load(), maxActive.Load())
	}
	if inflight, err := client.ZCard(ctx, keys[4]).Result(); err != nil || inflight != workers {
		t.Fatalf("inflight=%d err=%v", inflight, err)
	}
	if ready, err := client.ZCard(ctx, keys[3]).Result(); err != nil || ready != tasks-workers {
		t.Fatalf("ready=%d err=%v", ready, err)
	}
	close(release)
	results := takeRunnerResults(t, runner, tasks)
	for _, result := range results {
		if result.Err != nil {
			t.Fatalf("result %s: %v", result.JobID, result.Err)
		}
	}
	feeder.Close()
	select {
	case _, ok := <-runner.Results():
		if ok {
			t.Fatal("unexpected trailing result")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("runner did not close")
	}
	if maxActive.Load() != workers {
		t.Fatalf("maximum concurrency=%d", maxActive.Load())
	}
	if inflight, err := client.ZCard(ctx, keys[4]).Result(); err != nil || inflight != 0 {
		t.Fatalf("final inflight=%d err=%v", inflight, err)
	}
	if ready, err := client.ZCard(ctx, keys[3]).Result(); err != nil || ready != 0 {
		t.Fatalf("final ready=%d err=%v", ready, err)
	}
	if terminal, err := client.SCard(ctx, keys[6]).Result(); err != nil || terminal != tasks {
		t.Fatalf("final terminal=%d err=%v", terminal, err)
	}
	cleanup()
}

func readLifecycleLua(t *testing.T) string {
	t.Helper()
	_, sourceFile, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot locate integration test source")
	}
	luaPath := filepath.Join(
		filepath.Dir(sourceFile), "..", "..", "..",
		"apps", "crawler", "contracts", "queue", "v2", "redis", "lifecycle.lua",
	)
	luaSource, err := os.ReadFile(filepath.Clean(luaPath))
	if err != nil {
		t.Fatal(err)
	}
	return string(luaSource)
}

func takeRunnerResults(t *testing.T, runner *queueworker.Runner, count int) []worker.Result {
	t.Helper()
	results := make([]worker.Result, 0, count)
	timer := time.NewTimer(5 * time.Second)
	defer timer.Stop()
	for len(results) < count {
		select {
		case result, ok := <-runner.Results():
			if !ok {
				t.Fatalf("results closed after %d of %d", len(results), count)
			}
			results = append(results, result)
		case <-timer.C:
			t.Fatalf("timed out after %d of %d results", len(results), count)
		}
	}
	return results
}

func snapshotRedisKeys(
	t *testing.T,
	ctx context.Context,
	client *redis.Client,
	keys []string,
) []redisKeySnapshot {
	t.Helper()
	snapshot := make([]redisKeySnapshot, len(keys))
	for index, key := range keys {
		kind, err := client.Type(ctx, key).Result()
		if err != nil {
			t.Fatalf("TYPE key %d: %v", index, err)
		}
		dump, err := client.Do(ctx, "DUMP", key).Text()
		if errors.Is(err, redis.Nil) {
			dump = ""
		} else if err != nil {
			t.Fatalf("DUMP key %d: %v", index, err)
		}
		snapshot[index] = redisKeySnapshot{Kind: kind, Dump: dump}
	}
	return snapshot
}

func assertPoisonReadOnly(
	t *testing.T,
	ctx context.Context,
	client *redis.Client,
	source *RedisDueSource,
	keys []string,
	poison func() error,
	restore func() error,
) {
	t.Helper()
	if err := poison(); err != nil {
		t.Fatal(err)
	}
	before := snapshotRedisKeys(t, ctx, client, keys)
	if _, err := source.ListDue(ctx, 3); !errors.Is(err, ErrInvalidSnapshot) {
		t.Fatalf("poisoned snapshot error=%v", err)
	}
	after := snapshotRedisKeys(t, ctx, client, keys)
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("poisoned read changed queue keys\nbefore=%+v\nafter=%+v", before, after)
	}
	if err := restore(); err != nil {
		t.Fatal(err)
	}
}
