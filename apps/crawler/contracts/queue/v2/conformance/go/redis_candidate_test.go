package queuev2

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	candidateLuaPath  = "../../redis/lifecycle.lua"
	sharedFixturePath = "../../redis/fixtures/lifecycle_scenarios.json"
)

type sharedFixture struct {
	ClientFaults []sharedFault `json:"client_faults"`
	Format       string        `json:"format"`
	NumericMax   int64         `json:"numeric_max"`
	Route        Route         `json:"route"`
	Steps        []sharedStep  `json:"steps"`
}

type sharedFault struct {
	ID            string   `json:"id"`
	LeaseLost     bool     `json:"lease_lost"`
	ServerMutated bool     `json:"server_mutated"`
	Transition    []string `json:"transition"`
}

type sharedStep struct {
	Action        string         `json:"action"`
	DelayMS       int64          `json:"delay_ms"`
	DurationMS    int64          `json:"duration_ms"`
	Expect        []string       `json:"expect"`
	ExpectCounts  map[string]int `json:"expect_counts"`
	ID            string         `json:"id"`
	LeaseTTLMS    int64          `json:"lease_ttl_ms"`
	MaxFailures   int64          `json:"max_failures"`
	Revision      int64          `json:"revision"`
	RouteOverride map[string]any `json:"route_override"`
	SaveToken     string         `json:"save_token"`
	TaskID        string         `json:"task_id"`
	Token         string         `json:"token"`
}

func readFile(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(filepath.Clean(path))
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func candidateLua(t *testing.T) string { return string(readFile(t, candidateLuaPath)) }

func loadSharedFixture(t *testing.T) sharedFixture {
	t.Helper()
	var fixture sharedFixture
	decoder := json.NewDecoder(filepathReader(t, sharedFixturePath))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.Format != "jobseek.queue.v2.redis-lifecycle/v1" ||
		fixture.NumericMax != RedisCandidateMaxInteger {
		t.Fatalf("unexpected fixture contract: %+v", fixture)
	}
	return fixture
}

func filepathReader(t *testing.T, path string) *os.File {
	t.Helper()
	file, err := os.Open(filepath.Clean(path))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = file.Close() })
	return file
}

func TestRedisCandidateSevenKeysAndBoundedGoInputs(t *testing.T) {
	queue, err := NewRedisCandidateClient(nil, "safe-namespace", candidateLua(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(queue.Keys()) != 7 {
		t.Fatalf("got %d keys", len(queue.Keys()))
	}
	if _, err := NewRedisCandidateClient(nil, "unsafe{slot}", "return {}"); err == nil {
		t.Fatal("unsafe namespace was accepted")
	}
	base := RedisCandidateOperation{
		Kind: "claim", TaskID: "task",
		Route:          Route{ShardID: "shard", RoutingEpoch: 1, EngineOwner: "go"},
		ConfigRevision: 1, LeaseTTLMS: 1,
	}
	if err := validateRedisOperation(base); err != nil {
		t.Fatal(err)
	}
	base.Route.RoutingEpoch = RedisCandidateMaxInteger
	base.ConfigRevision = RedisCandidateMaxInteger
	base.LeaseTTLMS = RedisCandidateMaxInteger
	if err := validateRedisOperation(base); err != nil {
		t.Fatalf("numeric max rejected: %v", err)
	}
	base.Route.RoutingEpoch = RedisCandidateMaxInteger + 1
	if err := validateRedisOperation(base); err == nil {
		t.Fatal("numeric max+1 accepted")
	}
}

func TestOperationAwareGoDecoderRejectsImpossibleReplies(t *testing.T) {
	heartbeat := RedisCandidateOperation{
		Kind: "heartbeat", TaskID: "task",
		Route:          Route{ShardID: "shard", RoutingEpoch: 7, EngineOwner: "go"},
		ConfigRevision: 1, ClaimToken: "7:1", LeaseTTLMS: 100,
		PreviousLeaseUntil: 150,
	}
	invalid := [][]any{
		{"accepted", "completed", "100", "", ""},
		{"accepted", "claimed", "100", "7:1e3", "200"},
		{"accepted", "lease_extended", "100", "7:1", "150"},
		{"accepted", "registered", "0100", "", "100"},
		{"accepted", "registered", "10000000000000", "", "10000000000000"},
		{"fenced", "lease_expired", "100", "", ""},
		{"not_current", "not_due", "100", "7:1", "200"},
		{"transport-error", "redis_error", "0", "", ""},
		{"accepted", "lease_extended", int64(100), "7:1", "200"},
	}
	for _, raw := range invalid {
		if got := decodeRedisCandidateReply(heartbeat, raw); got != invalidRedisReply() {
			t.Fatalf("impossible reply accepted: %#v -> %+v", raw, got)
		}
	}
	valid := []any{"accepted", "lease_extended", "100", "7:1", "200"}
	got := decodeRedisCandidateReply(heartbeat, valid)
	if got.Decision != RedisAccepted || got.Value != 200 || !got.HasValue {
		t.Fatalf("valid heartbeat rejected: %+v", got)
	}
	complete := RedisCandidateOperation{
		Kind: "complete", TaskID: "task",
		Route:          Route{ShardID: "shard", RoutingEpoch: 7, EngineOwner: "go"},
		ConfigRevision: 1, ClaimToken: "7:1",
	}
	for _, token := range []string{"", "7:2"} {
		raw := []any{"accepted", "completed", "100", token, ""}
		if got := decodeRedisCandidateReply(complete, raw); got != invalidRedisReply() {
			t.Fatalf("complete accepted wrong token %q: %+v", token, got)
		}
	}
}

func routeForStep(t *testing.T, base Route, step sharedStep) Route {
	t.Helper()
	result := base
	for key, raw := range step.RouteOverride {
		switch key {
		case "shard_id":
			result.ShardID = raw.(string)
		case "routing_epoch":
			result.RoutingEpoch = int64(raw.(float64))
		case "engine_owner":
			result.EngineOwner = raw.(string)
		default:
			t.Fatalf("unknown route override %q", key)
		}
	}
	return result
}

func executeShared(
	t *testing.T,
	ctx context.Context,
	queue *RedisCandidateClient,
	operation RedisCandidateOperation,
) RedisTransition {
	t.Helper()
	result, err := queue.Execute(ctx, operation)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func runSharedScenarios(
	t *testing.T,
	ctx context.Context,
	client *redis.Client,
	namespace string,
) {
	t.Helper()
	fixture := loadSharedFixture(t)
	queue, err := NewRedisCandidateClient(client, namespace, candidateLua(t))
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := client.Del(context.Background(), queue.Keys()...).Err(); err != nil {
			t.Errorf("clean candidate keys: %v", err)
		}
	}()
	tokens := map[string]string{}
	leases := map[string]int64{}

	for _, step := range fixture.Steps {
		switch step.Action {
		case "sleep":
			time.Sleep(time.Duration(step.DurationMS) * time.Millisecond)
			continue
		case "restart_client":
			queue, err = NewRedisCandidateClient(client, namespace, candidateLua(t))
			if err != nil {
				t.Fatal(err)
			}
			continue
		case "concurrent_claim":
			operation := RedisCandidateOperation{
				Kind: "claim", TaskID: step.TaskID, Route: fixture.Route,
				ConfigRevision: step.Revision, LeaseTTLMS: step.LeaseTTLMS,
			}
			results := make(chan RedisTransition, 2)
			var wait sync.WaitGroup
			for range 2 {
				wait.Add(1)
				go func() {
					defer wait.Done()
					other, newErr := NewRedisCandidateClient(client, namespace, candidateLua(t))
					if newErr != nil {
						t.Errorf("new concurrent client: %v", newErr)
						return
					}
					result, executeErr := other.Execute(ctx, operation)
					if executeErr != nil {
						t.Errorf("concurrent claim: %v", executeErr)
						return
					}
					results <- result
				}()
			}
			wait.Wait()
			close(results)
			counts := map[string]int{}
			for result := range results {
				counts[string(result.Decision)+"/"+result.Reason]++
			}
			if !reflect.DeepEqual(counts, step.ExpectCounts) {
				t.Fatalf("%s: counts %#v != %#v", step.ID, counts, step.ExpectCounts)
			}
			if client.ZScore(ctx, queue.Keys()[4], step.TaskID).Err() != nil {
				t.Fatalf("%s: accepted task not exactly inflight", step.ID)
			}
			continue
		}

		route := routeForStep(t, fixture.Route, step)
		token := step.Token
		previousLease := int64(0)
		if alias, ok := parseTokenAlias(token); ok {
			token = tokens[alias]
			previousLease = leases[alias]
		}
		if step.Action == "heartbeat" && previousLease == 0 {
			previousLease = leases["fence-claim"]
		}
		operation := RedisCandidateOperation{
			Kind: step.Action, TaskID: step.TaskID, Route: route,
			ConfigRevision: step.Revision, ClaimToken: token,
			LeaseTTLMS: step.LeaseTTLMS, RescheduleDelayMS: step.DelayMS,
			MaxFailures: step.MaxFailures, PreviousLeaseUntil: previousLease,
		}
		result := executeShared(t, ctx, queue, operation)
		got := []string{string(result.Decision), result.Reason}
		if !reflect.DeepEqual(got, step.Expect) {
			t.Fatalf("%s: %v != %v", step.ID, got, step.Expect)
		}
		if step.SaveToken != "" {
			if result.ClaimToken == "" || !result.HasValue {
				t.Fatalf("%s did not return claim identity", step.ID)
			}
			tokens[step.SaveToken] = result.ClaimToken
			leases[step.SaveToken] = result.Value
		}
		if step.Action == "heartbeat" && result.Decision == RedisAccepted {
			alias, ok := parseTokenAlias(step.Token)
			if !ok || !result.HasValue {
				t.Fatalf("%s heartbeat identity missing", step.ID)
			}
			leases[alias] = result.Value
		}
	}

	routeState, err := client.HGetAll(ctx, queue.Keys()[0]).Result()
	if err != nil {
		t.Fatal(err)
	}
	wantRoute := map[string]string{
		"claim_sequence": "5", "engine_owner": "go",
		"routing_epoch": "7000000000001", "shard_id": "shared-shard",
	}
	if !reflect.DeepEqual(routeState, wantRoute) {
		t.Fatalf("route state %#v != %#v", routeState, wantRoute)
	}
}

type commandFaultHook struct {
	before bool
	after  bool
}

func (hook commandFaultHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, addr string) (net.Conn, error) {
		return next(ctx, network, addr)
	}
}

func (hook commandFaultHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, command redis.Cmder) error {
		isEval := command.Name() == "eval" || command.Name() == "evalsha"
		if hook.before && isEval {
			return errors.New("synthetic pre-execution transport failure")
		}
		err := next(ctx, command)
		if err == nil && hook.after && isEval {
			return errors.New("synthetic ambiguous post-execution transport failure")
		}
		return err
	}
}

func (hook commandFaultHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(ctx context.Context, commands []redis.Cmder) error { return next(ctx, commands) }
}

func faultByID(t *testing.T, fixture sharedFixture, id string) sharedFault {
	t.Helper()
	for _, fault := range fixture.ClientFaults {
		if fault.ID == id {
			return fault
		}
	}
	t.Fatalf("missing shared fault %q", id)
	return sharedFault{}
}

func prepareFaultClaim(
	t *testing.T, ctx context.Context, client *redis.Client, namespace string,
) (*RedisCandidateClient, RedisCandidateOperation, RedisTransition) {
	t.Helper()
	queue, err := NewRedisCandidateClient(client, namespace, candidateLua(t))
	if err != nil {
		t.Fatal(err)
	}
	route := Route{ShardID: "fault-shard", RoutingEpoch: 9, EngineOwner: "go"}
	for _, operation := range []RedisCandidateOperation{
		{Kind: "initialize", Route: route},
		{Kind: "register", TaskID: "fault-task", Route: route, ConfigRevision: 1},
	} {
		if result := executeShared(t, ctx, queue, operation); result.Decision != RedisAccepted {
			t.Fatalf("prepare fault claim: %+v", result)
		}
	}
	claim := RedisCandidateOperation{
		Kind: "claim", TaskID: "fault-task", Route: route,
		ConfigRevision: 1, LeaseTTLMS: 5_000,
	}
	result := executeShared(t, ctx, queue, claim)
	claim.ClaimToken = result.ClaimToken
	return queue, claim, result
}

func runSharedClientFaults(
	t *testing.T, ctx context.Context, options *redis.Options, ordinary *redis.Client,
) {
	t.Helper()
	fixture := loadSharedFixture(t)
	for _, id := range []string{
		"transport_before_execution", "invalid_success_reply", "ambiguous_transport_after_execution",
	} {
		fault := faultByID(t, fixture, id)
		if !fault.LeaseLost {
			t.Fatalf("fault %s does not require worker cancellation", id)
		}
		if id == "invalid_success_reply" {
			operation := RedisCandidateOperation{
				Kind: "complete", TaskID: "task",
				Route:          Route{ShardID: "shard", RoutingEpoch: 7, EngineOwner: "go"},
				ConfigRevision: 1, ClaimToken: "7:1",
			}
			result := decodeRedisCandidateReply(
				operation, []any{"accepted", "completed", "100", "", ""},
			)
			if !reflect.DeepEqual(
				[]string{string(result.Decision), result.Reason}, fault.Transition,
			) {
				t.Fatalf("invalid reply fault mismatch: %+v", result)
			}
			continue
		}

		namespace := fmt.Sprintf("%s-%d", id, time.Now().UnixNano())
		queue, claim, _ := prepareFaultClaim(t, ctx, ordinary, namespace)
		faultOptions := *options
		faultOptions.MaxRetries = -1
		faultClient := redis.NewClient(&faultOptions)
		faultClient.AddHook(commandFaultHook{
			before: id == "transport_before_execution",
			after:  id == "ambiguous_transport_after_execution",
		})
		faultQueue, err := NewRedisCandidateClient(faultClient, namespace, candidateLua(t))
		if err != nil {
			t.Fatal(err)
		}
		claim.Kind = "complete"
		claim.LeaseTTLMS = 0
		result := executeShared(t, ctx, faultQueue, claim)
		_ = faultClient.Close()
		if !reflect.DeepEqual([]string{string(result.Decision), result.Reason}, fault.Transition) {
			t.Fatalf("%s transition %+v", id, result)
		}
		terminal, err := ordinary.SIsMember(ctx, queue.Keys()[6], "fault-task").Result()
		if err != nil {
			t.Fatal(err)
		}
		if terminal != fault.ServerMutated {
			t.Fatalf("%s server mutation=%v want %v", id, terminal, fault.ServerMutated)
		}
		if err := ordinary.Del(ctx, queue.Keys()...).Err(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRealRedisRunsFullSharedLifecycleAndFaultScenarios(t *testing.T) {
	redisURL := os.Getenv("QUEUE_V2_REDIS_URL")
	if redisURL == "" || os.Getenv("QUEUE_V2_REDIS_ISOLATED") != "1" {
		t.Skip("requires QUEUE_V2_REDIS_URL and QUEUE_V2_REDIS_ISOLATED=1")
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	defer client.Close()
	if size := client.DBSize(ctx).Val(); size != 0 {
		t.Fatalf("Redis DB must start empty, got %d", size)
	}
	runSharedScenarios(t, ctx, client, fmt.Sprintf("real-go-%d", time.Now().UnixNano()))
	runSharedClientFaults(t, ctx, options, client)
	if size := client.DBSize(ctx).Val(); size != 0 {
		t.Fatalf("Redis DB must end empty, got %d", size)
	}
}
