//go:build integration && linux

package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
)

// This test is explicitly compiled and run as root on a disposable CI runner.
// It uses the fixed production socket and UID, real canonical PG/Redis sources,
// actual producer assignment, and the separately source-bound native CLI.
func TestInstalledNativeColdB0ForwardPreparesAndRetainsExactManifest(t *testing.T) {
	producerBinary := os.Getenv("JOBSEEK_B0_FORWARD_PRODUCER_BINARY")
	workerBinary := os.Getenv("JOBSEEK_B0_FORWARD_WORKER_BINARY")
	source := os.Getenv("JOBSEEK_B0_FORWARD_SOURCE_REVISION")
	luaPath := os.Getenv("JOBSEEK_B0_FORWARD_LUA_FILE")
	if os.Geteuid() != 0 || !ownershipRevision.MatchString(source) || !filepath.IsAbs(producerBinary) || !filepath.IsAbs(workerBinary) || !filepath.IsAbs(luaPath) {
		t.Fatal("explicit installed root fixture unavailable")
	}
	for _, binary := range []string{producerBinary, workerBinary} {
		if info, err := os.Stat(binary); err != nil || !info.Mode().IsRegular() || info.Mode()&0o111 == 0 {
			t.Fatal("installed fixture executable unavailable")
		}
	}
	const sentinel = "/run/jobseek-lightpanda-producer/.activation-v1"
	for _, path := range []string{sentinel, b0producer.SocketPath} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			t.Fatal("fixture refuses existing producer authority")
		}
	}
	metadata := `{"scraper_type":"json-ld","scraper_config":{"browser_backend":"lightpanda","render":true,"routing_revision":"go-b0-1","timeout":5000,"wait":"load","wait_fallback":null}}`
	p := realPublicationSeed(t, metadata, source, false)
	r := ColdB0ForwardRequest{p.intent, source, p.plan.Epoch(), p.plan.digest}
	seed := forwardFixtureLegacyPosting(t, p, true, "3", true)
	first := forwardFixtureLegacyPosting(t, p, true, "350.0001", true)
	future := forwardFixtureLegacyPosting(t, p, false, "1925089445.123001", true)
	pruned := forwardFixtureLegacyPosting(t, p, false, "0", false)
	ctx := context.Background()
	// Only this owned short private Redis directory/socket is shared with the
	// unprivileged producer. Nothing broadens production or test credentials.
	socket := p.f.client.redis.Options().Addr
	for _, path := range []string{filepath.Dir(socket), socket} {
		if err := os.Chown(path, 10001, 10001); err != nil {
			t.Fatal("private Redis fixture owner change failed")
		}
	}
	producer := exec.Command(producerBinary, "producer")
	producer.Env = []string{
		"LIGHTPANDA_B0_PRODUCER_MODE=enabled", "LIGHTPANDA_B0_PRODUCER_COHORT=c1",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID=0", "LIGHTPANDA_B0_PRODUCER_SOCKET=" + b0producer.SocketPath,
		"LIGHTPANDA_B0_QUEUE_NAMESPACE=" + p.target.document.Namespace, "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0",
		"LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(r.RoutingEpoch, 10), "LIGHTPANDA_B0_LUA_PATH=" + luaPath,
		"REDIS_URL=unix://" + socket, "LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST=" + strings.Repeat("a", 64),
		"LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256=" + strings.Repeat("b", 64),
	}
	producer.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 10001, Gid: 10001, NoSetGroups: true}}
	if err := producer.Start(); err != nil {
		t.Fatal("installed producer fixture failed to start")
	}
	done := make(chan error, 1)
	go func() { done <- producer.Wait() }()
	t.Cleanup(func() {
		_ = producer.Process.Kill()
		<-done
		_ = os.Remove(b0producer.SocketPath)
		_ = os.Remove(sentinel)
	})
	control := b0producer.NewClient()
	deadline := time.Now().Add(6 * time.Second)
	for {
		if _, err := control.Manifest(ctx, "c1"); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("fixed authenticated producer never became ready")
		}
		time.Sleep(25 * time.Millisecond)
	}
	seedConfig, err := p.f.client.redis.HGetAll(ctx, "scrape:"+seed).Result()
	if err != nil {
		t.Fatal("private seed source unavailable")
	}
	seedTask := b0producer.Task{PostingID: seed, Domain: "jobs.example.test", NextScrapeAtMS: 3000, Config: seedConfig, Browser: true, FirstTime: true, LegacyScheduleScore: "3"}
	prepared, err := control.Prepare(ctx, seedTask)
	if err != nil || prepared.Outcome != "prepared" {
		t.Fatal("actual seed preparation failed", err)
	}
	if _, err := control.Activate(ctx, seedTask, prepared.PreparationDigest); err != nil {
		t.Fatal("actual seed authority/sentinel activation failed", err)
	}
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	plan, err := BuildColdB0ForwardPlan(ctx, p.f.observer, p.f.client, control, r, p.target)
	if err != nil {
		t.Fatal("real authenticated producer/native PG preparation failed", err)
	}
	if plan.document.NewRecordCount != 2 || plan.document.ProjectedOccupancy != 3 || !reflect.DeepEqual(plan.document.UnqueuedPostingIDs, []string{pruned}) {
		t.Fatal("native source preparation lost conservation")
	}
	for _, task := range plan.document.Tasks {
		if task.PostingID == first && (!task.FirstTime || task.NextScrapeAtMS != 350001) || task.PostingID == future && (task.FirstTime || task.NextScrapeAtMS != 1925089445124) {
			t.Fatal("actual producer preparation changed due score semantics")
		}
	}
	directory := t.TempDir()
	write := func(name, body string) string {
		t.Helper()
		path := filepath.Join(directory, name)
		if os.WriteFile(path, []byte(body), 0o600) != nil {
			t.Fatal("protected fixture input unavailable")
		}
		return path
	}
	body, _ := json.Marshal(r)
	h := sha256.Sum256(body)
	env := map[string]string{
		"LOCAL_DATABASE_URL": p.f.dsn, "REDIS_URL": "unix://" + socket,
		"ORDINARY_OWNERSHIP_SOURCE_REVISION": source, "ORDINARY_COLD_ROUTING_EPOCH": strconv.FormatInt(r.RoutingEpoch, 10),
		"ORDINARY_COLD_INTENT_FILE": write("intent.json", mustColdSpec(t, p.spec)), "ORDINARY_COLD_INTENT_SHA256": p.intent,
		"ORDINARY_COLD_PLAN_SHA256": p.plan.digest, "ORDINARY_COLD_B0_TARGET_FILE": write("target.json", p.target.body),
		"ORDINARY_COLD_B0_TARGET_SHA256": p.target.digest, "ORDINARY_COLD_B0_LUA_FILE": write("queue.lua", p.target.lua),
		"ORDINARY_COLD_B0_FORWARD_REQUEST_FILE": write("request.json", string(body)), "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256": hex.EncodeToString(h[:]),
	}
	call := func(operation string, accepted bool) {
		t.Helper()
		command := exec.Command(workerBinary, "--"+operation)
		command.Env = []string{"ORDINARY_GO_WORKER_MODE=" + operation}
		for k, v := range env {
			command.Env = append(command.Env, k+"="+v)
		}
		output, err := command.CombinedOutput()
		if !accepted {
			if err == nil || !strings.Contains(string(output), "ordinary cold coordinator") || strings.Contains(string(output), p.f.dsn) {
				t.Fatal("protected native command admitted drift or exposed input")
			}
			return
		}
		var identity struct {
			Operation string          `json:"operation"`
			Source    string          `json:"source_revision"`
			Digest    string          `json:"b0_forward_plan_sha256"`
			Plan      json.RawMessage `json:"b0_forward_plan"`
		}
		if err != nil || json.Unmarshal(output, &identity) != nil || identity.Operation != operation || identity.Source != source || identity.Digest != plan.digest || string(identity.Plan) != plan.body {
			t.Fatal("installed native command lost exact retained manifest", operation)
		}
	}
	call("cold-b0-forward-plan", true)
	env["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = strings.Repeat("f", 64)
	call("cold-b0-forward-retain", false)
	env["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = plan.digest
	call("cold-b0-forward-retain", true)
	call("cold-b0-forward-retain", true)
	for _, key := range []string{"ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", "ORDINARY_COLD_B0_LUA_FILE"} {
		delete(env, key)
	}
	env["REDIS_URL"] = "unix:///private/unavailable/redis.sock"
	lock, err := p.f.observer.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	defer lock.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier)
	call("cold-b0-forward-inspect", true)
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reserved" {
		t.Fatal("real native preview/retention selected ownership or mutated source")
	}
}

func mustColdSpec(t *testing.T, spec ColdTransitionSpec) string {
	t.Helper()
	body, err := json.Marshal(spec)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}
