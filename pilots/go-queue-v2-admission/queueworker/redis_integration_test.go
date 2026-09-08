package queueworker

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"testing"
	"time"

	queuev2 "github.com/colophon-group/jobseek/apps/crawler/contracts/queue/v2/conformance/go"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
	"github.com/redis/go-redis/v9"
)

const isolatedAdmissionRedisURL = "redis://127.0.0.1:6381/15"

type redisLossClient struct {
	base     *queuev2.RedisCandidateClient
	lossTask string

	mu                  sync.Mutex
	lossInjected        bool
	lossClaim           queuev2.RedisTransition
	heartbeatObserved   chan struct{}
	heartbeatSignalOnce sync.Once
}

func (c *redisLossClient) Execute(
	ctx context.Context,
	operation queuev2.RedisCandidateOperation,
) (queuev2.RedisTransition, error) {
	if operation.Kind == "heartbeat" && operation.TaskID == c.lossTask {
		c.mu.Lock()
		if !c.lossInjected {
			c.lossInjected = true
			c.mu.Unlock()
			return queuev2.RedisTransition{
				Decision: queuev2.RedisFenced, Reason: "claim_token_mismatch",
				ServerTimeMS: operation.PreviousLeaseUntil - 1,
			}, nil
		}
		c.mu.Unlock()
	}
	transition, err := c.base.Execute(ctx, operation)
	if err == nil && operation.Kind == "claim" && operation.TaskID == c.lossTask &&
		transition.Decision == queuev2.RedisAccepted {
		c.mu.Lock()
		c.lossClaim = transition
		c.mu.Unlock()
	}
	if err == nil && operation.Kind == "heartbeat" && operation.TaskID == "complete" &&
		transition.Decision == queuev2.RedisAccepted {
		c.heartbeatSignalOnce.Do(func() { close(c.heartbeatObserved) })
	}
	return transition, err
}

func (c *redisLossClient) retainedLossClaim() queuev2.RedisTransition {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.lossClaim
}

func TestRealRedisAdmissionLifecycle(t *testing.T) {
	redisURL, urlSet := os.LookupEnv("QUEUE_V2_ADMISSION_REDIS_URL")
	isolated, isolatedSet := os.LookupEnv("QUEUE_V2_ADMISSION_REDIS_ISOLATED")
	if !urlSet && !isolatedSet {
		t.Skip("requires isolated queue-v2 admission Redis opt-in")
	}
	if !urlSet || !isolatedSet || isolated != "1" || redisURL != isolatedAdmissionRedisURL {
		t.Fatal("queue-v2 admission Redis test requires both opt-ins and exact isolated redis://127.0.0.1:6381/15")
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	if options.Addr != "127.0.0.1:6381" || options.DB != 15 ||
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
	namespace := fmt.Sprintf("admission-%d", time.Now().UnixNano())
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

	route := queuev2.Route{ShardID: "admission-shard", RoutingEpoch: 17, EngineOwner: "go"}
	initialize, err := queue.Execute(ctx, queuev2.RedisCandidateOperation{Kind: "initialize", Route: route})
	if err != nil || initialize.Decision != queuev2.RedisAccepted || initialize.Reason != "initialized" {
		t.Fatalf("initialize: %+v err=%v", initialize, err)
	}
	for _, taskID := range []string{"complete", "reschedule", "duplicate", "loss"} {
		registered, executeErr := queue.Execute(ctx, queuev2.RedisCandidateOperation{
			Kind: "register", TaskID: taskID, Route: route, ConfigRevision: 23,
		})
		if executeErr != nil || registered.Decision != queuev2.RedisAccepted || registered.Reason != "registered" {
			t.Fatalf("register %s: %+v err=%v", taskID, registered, executeErr)
		}
	}

	lossClient := &redisLossClient{
		base: queue, lossTask: "loss", heartbeatObserved: make(chan struct{}),
	}
	config := testConfig(4, 5, 6, 2)
	config.Route = route
	config.LeaseTTLMS = 1_000
	config.HeartbeatInterval = 100 * time.Millisecond
	config.HeartbeatTimeout = 250 * time.Millisecond
	config.TerminalTimeout = 500 * time.Millisecond
	var executionMu sync.Mutex
	executions := make(map[string]int)
	runner, err := New(config, lossClient, ExecutorFunc(func(
		workCtx context.Context,
		job worker.Job,
		_ queuev2.LeaseFence,
	) (sitemap.Result, Disposition, error) {
		executionMu.Lock()
		executions[job.ID]++
		executionAttempt := executions[job.ID]
		executionMu.Unlock()
		switch job.ID {
		case "complete":
			select {
			case <-lossClient.heartbeatObserved:
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
			case <-workCtx.Done():
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, workCtx.Err()
			}
		case "reschedule":
			return sitemap.Result{}, Disposition{
				Kind: DispositionReschedule, RescheduleDelay: 10 * time.Second,
			}, nil
		case "loss":
			if executionAttempt == 1 {
				<-workCtx.Done()
				var loss *queuev2.LeaseLostError
				if !errors.As(context.Cause(workCtx), &loss) {
					return sitemap.Result{}, Disposition{Kind: DispositionComplete},
						fmt.Errorf("unexpected first loss cause: %v", context.Cause(workCtx))
				}
				return sitemap.Result{}, Disposition{Kind: DispositionComplete}, workCtx.Err()
			}
			// A reaped recovery receives the next claim token and completes.
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
		default:
			return sitemap.Result{}, Disposition{Kind: DispositionComplete}, nil
		}
	}))
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range []Candidate{
		testCandidate("complete", "https://complete.example"),
		testCandidate("reschedule", "https://reschedule.example"),
		testCandidate("duplicate", "https://duplicate.example"),
		testCandidate("duplicate", "https://duplicate.example"),
		testCandidate("loss", "https://loss.example"),
	} {
		candidate.ConfigRevision = 23
		submitCandidate(t, runner, candidate)
	}
	initialResults := takeResults(t, runner, 5)
	claimRejected := 0
	leaseLost := 0
	for _, result := range initialResults {
		if errors.Is(result.Err, ErrClaimNotAccepted) {
			claimRejected++
		}
		var loss *queuev2.LeaseLostError
		if errors.As(result.Err, &loss) {
			leaseLost++
		}
	}
	if claimRejected != 1 || leaseLost != 1 {
		t.Fatalf("initial results claim_rejected=%d lease_lost=%d: %+v", claimRejected, leaseLost, initialResults)
	}
	lostClaim := lossClient.retainedLossClaim()
	if lostClaim.ClaimToken == "" || !lostClaim.HasValue {
		t.Fatalf("loss claim not retained: %+v", lostClaim)
	}
	reap := queuev2.RedisCandidateOperation{
		Kind: "reap", TaskID: "loss", Route: route, ConfigRevision: 23,
		ClaimToken: lostClaim.ClaimToken, MaxFailures: 2,
	}
	reaped := executeReapAfterExpiry(t, ctx, queue, reap)
	if reaped.Decision != queuev2.RedisAccepted || reaped.Reason != "requeued" ||
		!reaped.HasValue || reaped.Value != 1 {
		t.Fatalf("reap: %+v", reaped)
	}
	recovery := testCandidate("loss", "https://loss.example")
	recovery.ConfigRevision = 23
	submitCandidate(t, runner, recovery)
	if result := takeResults(t, runner, 1)[0]; result.Err != nil {
		t.Fatalf("recovered loss result: %v", result.Err)
	}
	runner.Close()
	if trailing := drainResults(t, runner); len(trailing) != 0 {
		t.Fatalf("unexpected trailing results: %+v", trailing)
	}

	executionMu.Lock()
	if executions["complete"] != 1 || executions["reschedule"] != 1 ||
		executions["duplicate"] != 1 || executions["loss"] != 2 {
		t.Fatalf("executions=%v", executions)
	}
	executionMu.Unlock()
	if inflight, err := client.ZCard(ctx, keys[4]).Result(); err != nil || inflight != 0 {
		t.Fatalf("inflight=%d err=%v", inflight, err)
	}
	if ready, err := client.ZCard(ctx, keys[3]).Result(); err != nil || ready != 1 {
		t.Fatalf("ready=%d err=%v", ready, err)
	}
	if terminal, err := client.SCard(ctx, keys[6]).Result(); err != nil || terminal != 3 {
		t.Fatalf("terminal=%d err=%v", terminal, err)
	}
	cleanup()
}

func takeResults(t *testing.T, runner *Runner, count int) []worker.Result {
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

func executeReapAfterExpiry(
	t *testing.T,
	ctx context.Context,
	queue *queuev2.RedisCandidateClient,
	operation queuev2.RedisCandidateOperation,
) queuev2.RedisTransition {
	t.Helper()
	for attempt := 0; attempt < 3; attempt++ {
		transition, err := queue.Execute(ctx, operation)
		if err != nil {
			t.Fatal(err)
		}
		if transition.Decision == queuev2.RedisAccepted {
			return transition
		}
		if transition.Decision != queuev2.RedisNotCurrent || transition.Reason != "lease_active" ||
			!transition.HasValue || transition.Value <= transition.ServerTimeMS {
			t.Fatalf("unexpected pre-expiry reap: %+v", transition)
		}
		wait := time.Duration(transition.Value-transition.ServerTimeMS+2) * time.Millisecond
		timer := time.NewTimer(wait)
		select {
		case <-timer.C:
		case <-ctx.Done():
			timer.Stop()
			t.Fatal(ctx.Err())
		}
	}
	t.Fatal("lease did not become reapable")
	return queuev2.RedisTransition{}
}
