//go:build integration

package queue

import (
	"context"
	"crypto/sha256"
	"debug/buildinfo"
	"encoding/hex"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// This immutable source predates finalization migration 0047 and its CLI. The
// actual old runtime, rather than a current reader with an old linked label,
// must consume the restored existing v1 authority at R.
const priorOrdinaryExecutableSource = "3cccd9fc33e8251ee8b043d8c6e91a3806c7f5b4"

func TestPriorNativeExecutableConsumesRestoredV1AuthorityAtR(t *testing.T) {
	binary := os.Getenv("JOBSEEK_ORDINARY_PRIOR_BINARY")
	data := os.Getenv("JOBSEEK_ORDINARY_PRIOR_DATA_DIRECTORY")
	expectedHash := os.Getenv("JOBSEEK_ORDINARY_PRIOR_BINARY_SHA256")
	if binary == "" && data == "" && expectedHash == "" {
		if os.Getenv("JOBSEEK_ORDINARY_PRIOR_REQUIRE") == "1" {
			t.Fatal("required independent prior executable unavailable")
		}
		t.Skip("explicit independently built prior executable required")
	}
	if !filepath.IsAbs(binary) || !filepath.IsAbs(data) || !ownershipSHA256.MatchString(expectedHash) {
		t.Fatal("prior executable requires absolute paths and exact hash")
	}
	checkBinary := func() {
		t.Helper()
		body, err := os.ReadFile(binary)
		digest := sha256.Sum256(body)
		if err != nil || hex.EncodeToString(digest[:]) != expectedHash {
			t.Fatal("prior executable bytes drifted")
		}
		metadata, err := buildinfo.ReadFile(binary)
		if err != nil {
			t.Fatal("prior executable build metadata missing")
		}
		settings := map[string]string{}
		for _, setting := range metadata.Settings {
			settings[setting.Key] = setting.Value
		}
		if settings["vcs.revision"] != priorOrdinaryExecutableSource || settings["vcs.modified"] != "false" {
			t.Fatal("prior executable is not built from the pinned clean checkout")
		}
	}
	checkBinary()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	identityOutput, err := exec.CommandContext(ctx, binary, "--identity").Output()
	var identity struct {
		Source  string `json:"source_revision"`
		CA      string `json:"ca_sha256"`
		Profile string `json:"profile"`
	}
	if err != nil || json.Unmarshal(identityOutput, &identity) != nil || identity.Source != priorOrdinaryExecutableSource || identity.CA != "fc9165a12403263e7ebfbdad7be7a3eac0fa5d325d3c70465f28d3690072ca28" || identity.Profile != greenhouseOwnershipProfile {
		t.Fatal("actual prior executable lost source/CA/profile identity")
	}
	for _, name := range []string{"occupations.csv", "technologies.csv"} {
		actual, err := os.ReadFile(filepath.Join(data, name))
		expected, gitErr := exec.CommandContext(ctx, "git", "show", priorOrdinaryExecutableSource+":apps/crawler/data/"+name).Output()
		if err != nil || gitErr != nil || string(actual) != string(expected) {
			t.Fatal("prior runtime taxonomy differs from immutable source", name)
		}
	}
	p, plan, control := finalizationFixtureWithPriorSource(t, true, false, priorOrdinaryExecutableSource)
	if frozen, err := p.f.client.redis.Persist(ctx, "ratelimit:jobs.example.test").Result(); err != nil || !frozen {
		t.Fatal("private prior runtime rate baseline not frozen")
	}
	ordinary, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, plan.Request().OrdinaryRestorationPlanSHA256, p.spec.SourceRevision)
	if err != nil || ordinary.fresh == nil || ordinary.fresh.SourceRevision() != priorOrdinaryExecutableSource {
		t.Fatal("fresh restored owner does not bind actual prior source")
	}
	// The migrated fixture lacks the separately imported runtime references.
	// Load the prior source's actual startup fixture into a private schema; never
	// overwrite canonical/public tables or claim production-cardinality evidence.
	schema := "prior_refs_" + strings.ReplaceAll(ordinaryID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := p.f.observer.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = p.f.observer.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	references, err := exec.CommandContext(ctx, "git", "show", priorOrdinaryExecutableSource+":apps/crawler/go/lightpanda-b0-executor/testdata/startup-fixture.sql").Output()
	if err != nil {
		t.Fatal("prior startup reference fixture missing")
	}
	if _, err := p.f.observer.Exec(ctx, strings.ReplaceAll(string(references), "public.", quoted+".")); err != nil {
		t.Fatal("prior startup references not loaded")
	}
	runtimeDSN, err := url.Parse(p.f.dsn)
	if err != nil {
		t.Fatal(err)
	}
	query := runtimeDSN.Query()
	query.Set("search_path", schema+",public")
	runtimeDSN.RawQuery = query.Encode()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	_ = listener.Close()
	luaPath := filepath.Join(t.TempDir(), "joint.lua")
	if err := os.WriteFile(luaPath, []byte(p.target.lua), 0600); err != nil {
		t.Fatal(err)
	}
	env := []string{"ORDINARY_GO_WORKER_MODE=enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + priorOrdinaryExecutableSource,
		"ORDINARY_OWNERSHIP_PLAN_SHA256=" + ordinary.fresh.digest, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + ordinary.fresh.ProjectionSHA1(),
		"ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + strconv.FormatInt(plan.RetirementEpoch(), 10), "ORDINARY_GO_B0_AUDIT_LUA_FILE=" + luaPath,
		"LOCAL_DATABASE_URL=" + runtimeDSN.String(), "REDIS_URL=unix://" + p.f.client.redis.Options().Addr,
		"ORDINARY_GO_METRICS_ADDRESS=" + address, "ORDINARY_GO_DATA_DIRECTORY=" + data,
		"DISCOVERY_CONCURRENCY=1", "MONITOR_CONCURRENCY=1", "SHUTDOWN_GRACE_SECONDS=1"}
	refuse := func(extra ...string) {
		t.Helper()
		bounded, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		cmd := exec.CommandContext(bounded, binary)
		cmd.Env = append(append([]string{}, env...), extra...)
		output, err := cmd.CombinedOutput()
		message := strings.TrimSpace(string(output))
		if err == nil || bounded.Err() != nil || (message != "ordinary worker failed" && message != "ordinary worker environment rejected") {
			t.Fatal("actual prior startup admitted drift or failed to reject promptly")
		}
	}
	before := forwardRedisSnapshot(t, p.f.client)
	refuse() // A freshly staged plan and approval are not authority.
	assertFinalizationPriorSnapshot(t, before, forwardRedisSnapshot(t, p.f.client))
	if _, err := publishColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	published := forwardRedisSnapshot(t, p.f.client)
	refuse() // Saved projection is insufficient while SQL remains reversing.
	assertFinalizationPriorSnapshot(t, published, forwardRedisSnapshot(t, p.f.client))
	restartPublicationRedisWithoutSave(t, p.f.client)
	assertFinalizationPriorSnapshot(t, published, forwardRedisSnapshot(t, p.f.client))
	if _, err := completeColdOrdinaryFinalization(ctx, p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	refuse("ORDINARY_OWNERSHIP_SOURCE_REVISION=" + strings.Repeat("d", 40))
	refuse("ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + strings.Repeat("d", 40))
	assertFinalizationPriorSnapshot(t, published, forwardRedisSnapshot(t, p.f.client))
	// Exercise a real owned no-fetch policy cycle: this must preserve publisher
	// reservation semantics, canonical due, failure budget and queue ACK at R.
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	cmd := exec.CommandContext(ctx, binary)
	cmd.Env = env
	if err := cmd.Start(); err != nil {
		t.Fatal("actual prior process failed to start")
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	waited := false
	t.Cleanup(func() {
		if !waited {
			_ = cmd.Process.Kill()
			<-done
		}
	})
	httpClient := &http.Client{Timeout: time.Second}
	defer httpClient.CloseIdleConnections()
	settled := false
	for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); {
		select {
		case <-done:
			waited = true
			t.Fatal("actual prior process exited before restored cycle")
		default:
		}
		var state string
		var epoch int64
		_ = p.f.observer.QueryRow(ctx, "SELECT state,routing_epoch FROM ordinary_worker_write_fence WHERE task_kind='monitor' AND task_id=$1::uuid", p.f.task.ID).Scan(&state, &epoch)
		response, err := httpClient.Get("http://" + address + "/healthz")
		if err == nil {
			healthy := response.StatusCode == http.StatusNoContent
			_ = response.Body.Close()
			settled = healthy && state == "completed" && epoch == plan.RetirementEpoch() && p.f.client.redis.ZCard(ctx, "inflight:simple").Val() == 0
			if settled {
				metrics, err := httpClient.Get("http://" + address + "/metrics")
				settled = err == nil
				if err == nil {
					body, readErr := io.ReadAll(io.LimitReader(metrics.Body, 128<<10))
					_ = metrics.Body.Close()
					settled = readErr == nil && strings.Contains(string(body), `crawler_tasks_total{kind="monitor",status="tdm_reserved"} 1`) && strings.Contains(string(body), `crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 0`) && strings.Contains(string(body), "jobseek_ordinary_go_active_claims 0")
				}
				if settled {
					break
				}
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !settled {
		t.Fatal("actual prior process did not admit and settle restored R authority")
	}
	health := exec.CommandContext(ctx, binary, "--health")
	health.Env = env
	if err := health.Run(); err != nil {
		t.Fatal("actual prior process identity health refused")
	}
	if err := cmd.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		waited = true
		if err != nil {
			t.Fatal("actual prior process failed signal drain")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("actual prior process did not drain")
	}
	var due time.Time
	var failures int
	if err := p.f.observer.QueryRow(ctx, "SELECT next_check_at,consecutive_failures FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&due, &failures); err != nil || failures != 0 {
		t.Fatal("prior policy cycle spent failure budget")
	}
	if score := p.f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", p.f.task.ID).Val(); score != float64(due.UnixMicro())/1e6 {
		t.Fatal("prior runtime lost canonical due/queue conservation")
	}
	var epoch int64
	if err := p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != plan.RetirementEpoch() {
		t.Fatal("prior runtime adopted another epoch")
	}
	var originalState, restoredState string
	if err := p.f.observer.QueryRow(ctx, "SELECT original.state,restored.state FROM ordinary_worker_ownership_plan original,ordinary_worker_ownership_plan restored WHERE original.plan_sha256=$1 AND restored.plan_sha256=$2", p.spec.PreviousOrdinaryPlanSHA256, ordinary.fresh.digest).Scan(&originalState, &restoredState); err != nil || originalState != "retired" || restoredState != "active" {
		t.Fatal("prior runtime changed restored or retired plan state")
	}
	assertCanonical(t, p.f, false)
	checkBinary()
}

func assertFinalizationPriorSnapshot(t *testing.T, before, after map[string]string) {
	t.Helper()
	if !reflect.DeepEqual(before, after) {
		t.Fatal("refused prior runtime changed queue or authority state")
	}
}
