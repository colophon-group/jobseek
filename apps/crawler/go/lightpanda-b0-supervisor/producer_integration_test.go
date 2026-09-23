//go:build integration && linux

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	"github.com/redis/go-redis/v9"
)

const (
	integrationBoardID = "11111111-1111-4111-8111-111111111111"
	integrationTaskID  = "00000000-0000-4000-8000-000000000001"
)

func installedProducerCommand(binary string, environment []string, producerUID uint32, arguments ...string) *exec.Cmd {
	command := exec.Command(binary, arguments...) //nolint:gosec // CI provides the just-built reviewed binary.
	command.Env = environment
	command.SysProcAttr = &syscall.SysProcAttr{
		Credential: &syscall.Credential{Uid: producerUID, Gid: producerUID, NoSetGroups: true},
	}
	return command
}

func environmentWith(base []string, overrides ...string) []string {
	keys := make(map[string]struct{}, len(overrides))
	for _, override := range overrides {
		key, _, _ := strings.Cut(override, "=")
		keys[key] = struct{}{}
	}
	result := make([]string, 0, len(base)+len(overrides))
	for _, entry := range base {
		key, _, _ := strings.Cut(entry, "=")
		if _, replaced := keys[key]; !replaced {
			result = append(result, entry)
		}
	}
	return append(result, overrides...)
}

func installedProducerHealth(binary string, environment []string, producerUID uint32) error {
	output, err := installedProducerCommand(binary, environment, producerUID, "producer", "--healthcheck").CombinedOutput()
	if err != nil {
		return fmt.Errorf("producer healthcheck: %w: %s", err, strings.TrimSpace(string(output)))
	}
	return nil
}

func waitForInstalledProducerReady(t *testing.T, binary string, environment []string, producerUID uint32) error {
	t.Helper()
	deadline := time.Now().Add(2 * producerTimeout)
	var lastError error
	for time.Now().Before(deadline) {
		lastError = installedProducerHealth(binary, environment, producerUID)
		if lastError == nil {
			return nil
		}
		time.Sleep(25 * time.Millisecond)
	}
	return fmt.Errorf("installed producer did not pass its authenticated health protocol: %w", lastError)
}

func TestProducerSelfUIDCannotMutate(t *testing.T) {
	if os.Getenv("LIGHTPANDA_B0_SELF_UID_HELPER") != "1" {
		t.Skip("self-UID subprocess helper")
	}
	connection, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: producerSocketPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(producerTimeout))
	request := producerRequest{
		Version: producerProtocol, Operation: "enqueue", Domain: "jobs.example.com",
		PostingID: integrationTaskID, Config: map[string]string{
			"board_id": integrationBoardID, "source_url": "https://jobs.example.com/posting",
			"scrape_step": "0", "scrape_interval_hours": "24", "description_r2_hash": "",
		}, Browser: true,
	}
	payload, err := canonicalJSON(request, true)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(payload, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connection.Write(record); err != nil {
		t.Fatal(err)
	}
	if _, err := framing.ReadRecord(connection, producerFrameLimit); err == nil {
		t.Fatal("producer self UID received a mutation response")
	}
}

func TestInstalledProducerOwnsRealRedisLifecycleAndFailsReadinessOnFence(t *testing.T) {
	binary := os.Getenv("LIGHTPANDA_B0_INTEGRATION_BINARY")
	redisURL := os.Getenv("LIGHTPANDA_B0_INTEGRATION_REDIS_URL")
	rawProducerUID := os.Getenv("LIGHTPANDA_B0_INTEGRATION_PRODUCER_UID")
	producerUID, uidErr := strconv.ParseUint(rawProducerUID, 10, 32)
	if binary == "" || redisURL == "" || rawProducerUID == "" {
		t.Skip("installed producer and integration Redis are not configured")
	}
	if os.Geteuid() != 0 || uidErr != nil || producerUID == 0 {
		t.Fatal("integration must run as root with a distinct unprivileged producer UID")
	}
	if binaryInfo, err := os.Stat(binary); err != nil || binaryInfo.Mode()&0o111 == 0 {
		t.Fatalf("integration producer is not an installed executable: %v", err)
	}
	options, err := redis.ParseURL(redisURL)
	if err != nil {
		t.Fatal(err)
	}
	client := redis.NewClient(options)
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := os.Remove(producerSentinelPath); err != nil && !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = client.FlushDB(context.Background()).Err()
		_ = os.Remove(producerSentinelPath)
	})

	luaPath := os.Getenv("LIGHTPANDA_B0_INTEGRATION_LUA_PATH")
	if luaPath == "" {
		luaPath, err = filepath.Abs("../../src/lua/lightpanda_b0_queue.lua")
		if err != nil {
			t.Fatal(err)
		}
	}
	const namespace = "producer-integration"
	bootstrapKey := "lightpanda-b0:{" + namespace + "}:route"
	if keyType, err := client.Type(ctx, bootstrapKey).Result(); err != nil || keyType != "none" {
		t.Fatalf("integration did not begin from the intended empty bootstrap namespace: %q %v", keyType, err)
	}
	parserConfig := map[string]any{
		"browser_backend": "lightpanda", "render": true, "routing_revision": "go-b0-1",
		"timeout": int64(5000), "wait": "load", "wait_fallback": nil,
	}
	metadata, err := canonicalJSON(map[string]any{"scraper_config": parserConfig, "scraper_type": "json-ld"}, true)
	if err != nil {
		t.Fatal(err)
	}
	if err := client.HSet(ctx, "board:"+integrationBoardID, map[string]any{
		"board_slug": "browser-use-careers", "metadata": string(metadata),
	}).Err(); err != nil {
		t.Fatal(err)
	}

	var stderr bytes.Buffer
	producerEnvironment := environmentWith(os.Environ(),
		"LIGHTPANDA_B0_PRODUCER_MODE=enabled",
		"LIGHTPANDA_B0_PRODUCER_COHORT=c1",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID=0",
		"LIGHTPANDA_B0_PRODUCER_SOCKET="+producerSocketPath,
		"LIGHTPANDA_B0_QUEUE_NAMESPACE="+namespace,
		"LIGHTPANDA_B0_SHARD_ID=lightpanda-b0",
		"LIGHTPANDA_B0_ROUTING_EPOCH=7",
		"LIGHTPANDA_B0_LUA_PATH="+luaPath,
		"REDIS_URL="+redisURL,
		"LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST="+strings.Repeat("a", 64),
		"LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256="+strings.Repeat("b", 64),
	)
	command := installedProducerCommand(binary, producerEnvironment, uint32(producerUID), "producer")
	command.Stderr = &stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	waited := make(chan error, 1)
	go func() { waited <- command.Wait() }()
	t.Cleanup(func() {
		if command.ProcessState == nil {
			_ = command.Process.Kill()
			<-waited
		}
	})
	if err := waitForInstalledProducerReady(t, binary, producerEnvironment, uint32(producerUID)); err != nil {
		_ = command.Process.Kill()
		<-waited
		t.Fatalf("%v; producer stderr: %q", err, stderr.String())
	}
	selfClientEnvironment := environmentWith(os.Environ(), "LIGHTPANDA_B0_SELF_UID_HELPER=1")
	selfClient := installedProducerCommand(os.Args[0], selfClientEnvironment, uint32(producerUID), "-test.run=^TestProducerSelfUIDCannotMutate$")
	if output, err := selfClient.CombinedOutput(); err != nil {
		t.Fatalf("self-UID mutation rejection failed: %v %s", err, output)
	}

	request := producerRequest{
		Version: producerProtocol, Operation: "enqueue", Domain: "jobs.example.com",
		PostingID: integrationTaskID, Config: map[string]string{
			"board_id": integrationBoardID, "source_url": "https://jobs.example.com/posting",
			"scrape_step": "0", "scrape_interval_hours": "24", "description_r2_hash": "",
		}, Browser: true,
	}
	first := controlExchange(t, producerSocketPath, request)
	if first.Outcome != "activated" || first.Reason != "activated" || !first.Activated {
		t.Fatalf("first real activation failed: %#v", first)
	}
	second := controlExchange(t, producerSocketPath, request)
	if second.Outcome != "activated" || second.Reason != "already_activated" || second.Activated {
		t.Fatalf("idempotent real activation failed: %#v", second)
	}
	sentinelInfo, err := os.Stat(producerSentinelPath)
	if err != nil {
		t.Fatal(err)
	}
	sentinelMetadata, ok := sentinelInfo.Sys().(*syscall.Stat_t)
	if !ok || sentinelInfo.Mode().Perm() != 0o600 || sentinelMetadata.Uid != uint32(producerUID) {
		t.Fatalf("activation sentinel metadata was not producer-owned and private: %#v", sentinelInfo)
	}
	ownerFields, err := client.HGetAll(ctx, producerOwnerKey).Result()
	if err != nil || ownerFields["schema"] != producerOwnerV1 || ownerFields["namespace"] != namespace ||
		ownerFields["cohort"] != "c1" || ownerFields["board_count"] != "1" ||
		ownerFields["board_slug:browser-use-careers"] != "1" {
		t.Fatalf("producer owner manifest was not exact: %#v %v", ownerFields, err)
	}

	route := routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner}
	queue, err := newB0Queue(client, luaPath, namespace, route, "2.0", newMetrics())
	if err != nil {
		t.Fatal(err)
	}
	current, result, err := queue.claim(ctx, time.Minute)
	if err != nil || current == nil || result.Reason != "claimed" {
		t.Fatalf("real queue claim failed: %#v %#v %v", current, result, err)
	}
	if err := queue.terminal(ctx, current, nil); err != nil {
		t.Fatal(err)
	}
	third := controlExchange(t, producerSocketPath, request)
	if third.Outcome != "activated" || third.Reason != "reactivated" || !third.Activated {
		t.Fatalf("terminal revision was not reactivated: %#v", third)
	}
	stored, err := queue.inspect(ctx, integrationTaskID)
	if err != nil || stored == nil || stored.Task.Envelope.ConfigRevision != 2 {
		t.Fatalf("terminal revision did not advance exactly: %#v %v", stored, err)
	}

	// Live health and the periodic monitor share the same bounded full audit.
	// Prove each independently stored integrity path trips authority after the
	// socket was already ready, then repair it for the next corruption class.
	rawBeforeCorruption, err := client.HGet(ctx, queue.keys[1], integrationTaskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	readyScore, err := client.ZScore(ctx, queue.keys[2], integrationTaskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	guardBeforeCorruption, err := client.HGet(ctx, legacyGuardKey, integrationTaskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	tripHealthCorruption := func(label string, corrupt, repair func() error) {
		t.Helper()
		if err := corrupt(); err != nil {
			t.Fatalf("%s corruption setup failed: %v", label, err)
		}
		if err := installedProducerHealth(binary, producerEnvironment, uint32(producerUID)); err == nil {
			t.Fatalf("%s corruption passed a full health audit", label)
		}
		select {
		case err := <-waited:
			if err == nil {
				t.Fatalf("%s-corrupt producer exited successfully", label)
			}
		case <-time.After(2 * producerTimeout):
			_ = command.Process.Kill()
			t.Fatalf("%s-corrupt producer did not terminate within the integration bound", label)
		}
		if stderr.String() != "Lightpanda B0 producer failed closed: producer authority lost: corruption/health\n" {
			t.Fatalf("%s health corruption log was not bounded: %q", label, stderr.String())
		}
		if err := repair(); err != nil {
			t.Fatalf("%s corruption repair failed: %v", label, err)
		}
		stderr.Reset()
		command = installedProducerCommand(binary, producerEnvironment, uint32(producerUID), "producer")
		command.Stderr = &stderr
		if err := command.Start(); err != nil {
			t.Fatal(err)
		}
		waited = make(chan error, 1)
		go func() { waited <- command.Wait() }()
		if err := waitForInstalledProducerReady(t, binary, producerEnvironment, uint32(producerUID)); err != nil {
			_ = command.Process.Kill()
			<-waited
			t.Fatalf("%v; producer stderr: %q", err, stderr.String())
		}
	}
	tripHealthCorruption("record",
		func() error { return client.HSet(ctx, queue.keys[1], integrationTaskID, `{}`).Err() },
		func() error { return client.HSet(ctx, queue.keys[1], integrationTaskID, rawBeforeCorruption).Err() },
	)
	tripHealthCorruption("index",
		func() error { return client.ZRem(ctx, queue.keys[2], integrationTaskID).Err() },
		func() error {
			return client.ZAdd(ctx, queue.keys[2], redis.Z{Score: readyScore, Member: integrationTaskID}).Err()
		},
	)
	tripHealthCorruption("guard",
		func() error { return client.HDel(ctx, legacyGuardKey, integrationTaskID).Err() },
		func() error {
			return client.HSet(ctx, legacyGuardKey, integrationTaskID, guardBeforeCorruption).Err()
		},
	)
	tripHealthCorruption("owner",
		func() error { return client.HSet(ctx, producerOwnerKey, "routing_epoch", "01").Err() },
		func() error { return client.HSet(ctx, producerOwnerKey, "routing_epoch", "7").Err() },
	)

	if err := client.HSet(ctx, queue.keys[0], "routing_epoch", "8").Err(); err != nil {
		t.Fatal(err)
	}
	fenced := controlExchange(t, producerSocketPath, request)
	if fenced.Outcome != "error" || fenced.Reason != "authority_lost" {
		t.Fatalf("fenced producer request was not closed: %#v", fenced)
	}
	select {
	case err := <-waited:
		if err == nil {
			t.Fatal("fenced producer exited successfully")
		}
	case <-ctx.Done():
		t.Fatal("fenced producer did not terminate within the integration bound")
	}
	if err := installedProducerHealth(binary, producerEnvironment, uint32(producerUID)); err == nil {
		t.Fatal("fenced producer remained ready")
	}
	wantLog := "Lightpanda B0 producer failed closed: producer authority lost: fenced/enqueue\n"
	if stderr.String() != wantLog {
		t.Fatalf("producer emitted an unbounded authority log: %q", stderr.String())
	}

	var restartStderr bytes.Buffer
	restart := installedProducerCommand(binary, producerEnvironment, uint32(producerUID), "producer")
	restart.Stderr = &restartStderr
	if err := restart.Start(); err != nil {
		t.Fatal(err)
	}
	restarted := make(chan error, 1)
	go func() { restarted <- restart.Wait() }()
	select {
	case err := <-restarted:
		if err == nil {
			t.Fatal("persistently fenced producer restart exited successfully")
		}
	case <-time.After(2 * producerTimeout):
		_ = restart.Process.Kill()
		t.Fatal("persistently fenced producer restart did not fail before readiness")
	}
	if restartStderr.String() != "Lightpanda B0 producer failed closed: producer authority lost: fenced/preflight\n" {
		t.Fatalf("persistent fence restart log was not bounded: %q", restartStderr.String())
	}
	if err := installedProducerHealth(binary, producerEnvironment, uint32(producerUID)); err == nil {
		t.Fatal("persistent fence restart became ready")
	}

	// Model a full E1 RDB restore after the host has moved to E2: route, owner,
	// records, and guards are all internally exact E1 state, while the durable
	// sentinel and producer config are E2. Startup must fence before readiness.
	if err := client.HSet(ctx, queue.keys[0], "routing_epoch", "7").Err(); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(producerSentinelPath); err != nil {
		t.Fatal(err)
	}
	epochTwoOwner := producerOwnerIdentity{
		Namespace: namespace, Cohort: "c1",
		Route:      routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 8, EngineOwner: engineOwner},
		BoardSlugs: []string{"browser-use-careers"},
	}
	epochTwoSentinel, err := newProducerActivationSentinel(
		producerSentinelPath, epochTwoOwner, uint32(producerUID),
	)
	if err != nil {
		t.Fatal(err)
	}
	if err := epochTwoSentinel.ensurePreparing(); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(producerSentinelPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := epochTwoSentinel.publishActive(); err != nil {
		t.Fatal(err)
	}
	epochTwoEnvironment := environmentWith(producerEnvironment, "LIGHTPANDA_B0_ROUTING_EPOCH=8")
	var epochTwoStderr bytes.Buffer
	epochTwo := installedProducerCommand(binary, epochTwoEnvironment, uint32(producerUID), "producer")
	epochTwo.Stderr = &epochTwoStderr
	if err := epochTwo.Start(); err != nil {
		t.Fatal(err)
	}
	if err := epochTwo.Wait(); err == nil {
		t.Fatal("E2 producer accepted fully restored E1 Redis authority")
	}
	if epochTwoStderr.String() != "Lightpanda B0 producer failed closed: producer authority lost: fenced/preflight\n" {
		t.Fatalf("E1 restore under E2 returned the wrong bounded failure: %q", epochTwoStderr.String())
	}
	if err := installedProducerHealth(binary, epochTwoEnvironment, uint32(producerUID)); err == nil {
		t.Fatal("E1 restore under E2 became ready")
	}

	// Restore the original E1 sentinel so the remaining loss/recovery matrix
	// continues to exercise the original incarnation.
	if err := os.Remove(producerSentinelPath); err != nil {
		t.Fatal(err)
	}
	epochOneSentinel, err := newProducerActivationSentinel(
		producerSentinelPath,
		producerOwnerIdentity{
			Namespace: namespace, Cohort: "c1",
			Route:      routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner},
			BoardSlugs: []string{"browser-use-careers"},
		},
		uint32(producerUID),
	)
	if err != nil {
		t.Fatal(err)
	}
	if err := epochOneSentinel.ensurePreparing(); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(producerSentinelPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := epochOneSentinel.publishActive(); err != nil {
		t.Fatal(err)
	}

	raw, err := client.HGet(ctx, queue.keys[1], integrationTaskID).Result()
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal([]byte(raw), &record); err != nil {
		t.Fatal(err)
	}
	if revision, ok := record["config_revision"].(float64); !ok || revision != 2 {
		t.Fatalf("fenced request mutated the terminal revision: %#v", record["config_revision"])
	}

	// A completed activation is never reclassified as first bootstrap, even
	// when all seven namespaced keys disappear together.
	if err := client.HSet(ctx, queue.keys[0], "routing_epoch", "7").Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.Del(ctx, queue.keys...).Err(); err != nil {
		t.Fatal(err)
	}
	var namespaceLossStderr bytes.Buffer
	namespaceLoss := installedProducerCommand(binary, producerEnvironment, uint32(producerUID), "producer")
	namespaceLoss.Stderr = &namespaceLossStderr
	if err := namespaceLoss.Start(); err != nil {
		t.Fatal(err)
	}
	if err := namespaceLoss.Wait(); err == nil {
		t.Fatal("all-seven namespace loss restart exited successfully")
	}
	if namespaceLossStderr.String() != "Lightpanda B0 producer failed closed: producer authority lost: corruption/preflight\n" {
		t.Fatalf("all-seven namespace loss log was not bounded: %q", namespaceLossStderr.String())
	}
	if err := installedProducerHealth(binary, producerEnvironment, uint32(producerUID)); err == nil {
		t.Fatal("all-seven namespace loss became ready")
	}

	// Whole-DB loss removes the Redis owner and guard too; the durable volume
	// sentinel still distinguishes it from a never-activated deployment.
	if err := client.FlushDB(ctx).Err(); err != nil {
		t.Fatal(err)
	}
	var databaseLossStderr bytes.Buffer
	databaseLoss := installedProducerCommand(binary, producerEnvironment, uint32(producerUID), "producer")
	databaseLoss.Stderr = &databaseLossStderr
	if err := databaseLoss.Start(); err != nil {
		t.Fatal(err)
	}
	if err := databaseLoss.Wait(); err == nil {
		t.Fatal("whole Redis loss restart exited successfully")
	}
	if databaseLossStderr.String() != "Lightpanda B0 producer failed closed: producer authority lost: corruption/preflight\n" {
		t.Fatalf("whole Redis loss log was not bounded: %q", databaseLossStderr.String())
	}
	if _, err := os.Stat(producerSentinelPath); err != nil {
		t.Fatalf("whole Redis loss removed the durable activation sentinel: %v", err)
	}
	if err := installedProducerHealth(binary, producerEnvironment, uint32(producerUID)); err == nil {
		t.Fatal("whole Redis loss became ready")
	}

	// A SIGKILL leaves the socket inode behind. The rollback one-shot accepts
	// only that exact producer-owned stale form after verifying Redis is fully
	// released, then removes/fsyncs it and clears the activation sentinel.
	// Truncation models a crash between O_EXCL marker creation and its write.
	if err := os.Truncate(producerSentinelPath, 0); err != nil {
		t.Fatal(err)
	}
	stale, err := net.ListenUnix("unix", &net.UnixAddr{Name: producerSocketPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	stale.SetUnlinkOnClose(false)
	if err := os.Chown(producerSocketPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(producerSocketPath, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := stale.Close(); err != nil {
		t.Fatal(err)
	}
	if err := client.Set(ctx, producerOwnerKey, "still-owned", 0).Err(); err != nil {
		t.Fatal(err)
	}
	if err := installedProducerCommand(
		binary, producerEnvironment, uint32(producerUID), "producer", "--clear-activation-sentinel",
	).Run(); err == nil {
		t.Fatal("rollback clear mutated local state before Redis absence proof")
	}
	if _, err := os.Lstat(producerSocketPath); err != nil {
		t.Fatalf("failed Redis proof removed stale socket: %v", err)
	}
	if info, err := os.Stat(producerSentinelPath); err != nil || info.Size() != 0 {
		t.Fatalf("failed Redis proof removed or changed partial sentinel: %#v %v", info, err)
	}
	if err := client.Del(ctx, producerOwnerKey).Err(); err != nil {
		t.Fatal(err)
	}
	if err := client.HSet(ctx, producerOwnerKey, map[string]any{
		"schema": "jobseek.lightpanda.producer-rollback/v1", "namespace": namespace,
		"shard_id": "lightpanda-b0", "routing_epoch": "7", "engine_owner": "go",
		"cohort": "c1", "rollback_plan_digest": strings.Repeat("a", 64),
		"source_receipt_sha256": strings.Repeat("b", 64),
	}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := installedProducerCommand(
		binary, producerEnvironment, uint32(producerUID), "producer", "--clear-activation-sentinel",
	).Run(); err != nil {
		t.Fatalf("stale socket rollback clear failed: %v", err)
	}
	if _, err := os.Lstat(producerSocketPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("rollback clear retained stale socket: %v", err)
	}
	if _, err := os.Stat(producerSentinelPath); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("rollback clear retained activation sentinel: %v", err)
	}

	// Unsafe and live paths are never treated as SIGKILL residue.
	if err := os.WriteFile(producerSocketPath, []byte("unsafe"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(producerSocketPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := installedProducerCommand(
		binary, producerEnvironment, uint32(producerUID), "producer", "--clear-activation-sentinel",
	).Run(); err == nil {
		t.Fatal("unsafe producer socket was cleared")
	}
	if err := os.Remove(producerSocketPath); err != nil {
		t.Fatal(err)
	}
	live, err := net.ListenUnix("unix", &net.UnixAddr{Name: producerSocketPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(producerSocketPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(producerSocketPath, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := installedProducerCommand(
		binary, producerEnvironment, uint32(producerUID), "producer", "--clear-activation-sentinel",
	).Run(); err == nil {
		t.Fatal("live producer socket was cleared")
	}
	if _, err := os.Lstat(producerSocketPath); err != nil {
		t.Fatalf("live producer socket was removed: %v", err)
	}
	if err := live.Close(); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(producerSentinelPath, []byte("unsafe"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(producerSentinelPath, int(producerUID), int(producerUID)); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(producerSentinelPath, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := installedProducerCommand(
		binary, producerEnvironment, uint32(producerUID), "producer", "--clear-activation-sentinel",
	).Run(); err == nil {
		t.Fatal("unsafe activation sentinel was cleared")
	}
	if _, err := os.Lstat(producerSentinelPath); err != nil {
		t.Fatalf("unsafe activation sentinel did not remain: %v", err)
	}
	if err := os.Remove(producerSentinelPath); err != nil {
		t.Fatal(err)
	}
	if ctx.Err() != nil {
		t.Fatalf("integration exceeded its deadline: %v", ctx.Err())
	}
}
