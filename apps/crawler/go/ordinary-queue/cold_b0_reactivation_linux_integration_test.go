//go:build integration && linux

package queue

import (
	"context"
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
	"github.com/jackc/pgx/v5"
)

// Root owns only this disposable fixture. Earlier B0 restoration uses actual
// queue Lua; reactivation uses the real UID-10001 producer and compiled CLI.
// This is not evidence of independently verified production rollback releases.
func TestInstalledNativeColdB0ReactivationUsesRetainedRollbackAtR(t *testing.T) {
	producerBinary, workerBinary := os.Getenv("JOBSEEK_B0_FORWARD_PRODUCER_BINARY"), os.Getenv("JOBSEEK_B0_FORWARD_WORKER_BINARY")
	source, luaPath := os.Getenv("JOBSEEK_B0_FORWARD_SOURCE_REVISION"), os.Getenv("JOBSEEK_B0_FORWARD_LUA_FILE")
	if os.Geteuid() != 0 || !ownershipRevision.MatchString(source) || !filepath.IsAbs(producerBinary) || !filepath.IsAbs(workerBinary) || !filepath.IsAbs(luaPath) {
		t.Fatal("explicit installed root reactivation fixture unavailable")
	}
	const sentinel = "/run/jobseek-lightpanda-producer/.activation-v1"
	for _, path := range []string{sentinel, b0producer.SocketPath} {
		if _, err := os.Lstat(path); !os.IsNotExist(err) {
			t.Fatal("reactivation fixture refuses existing authority")
		}
	}
	metadata := `{"scraper_type":"json-ld","scraper_config":{"browser_backend":"lightpanda","render":true,"routing_revision":"go-b0-1","timeout":5000,"wait":"load","wait_fallback":null}}`
	p, request, _, receipt := reactivationFixtureWithSource(t, true, metadata, source)
	ctx := context.Background()
	ordinary, err := InspectColdOrdinaryRestorationPlan(ctx, p.f.observer, request.OrdinaryRestorationPlanSHA256, source)
	if err != nil {
		t.Fatal(err)
	}
	r := ordinary.Request().RetirementEpoch
	socket := p.f.client.redis.Options().Addr
	for _, path := range []string{filepath.Dir(socket), socket} {
		if err := os.Chown(path, 10001, 10001); err != nil {
			t.Fatal("private reactivation Redis owner change failed")
		}
	}
	producerEnv := []string{
		"LIGHTPANDA_B0_PRODUCER_MODE=enabled", "LIGHTPANDA_B0_PRODUCER_COHORT=c1",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID=0", "LIGHTPANDA_B0_PRODUCER_SOCKET=" + b0producer.SocketPath,
		"LIGHTPANDA_B0_QUEUE_NAMESPACE=" + p.target.document.Namespace, "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0",
		"LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(r, 10), "LIGHTPANDA_B0_LUA_PATH=" + luaPath,
		"REDIS_URL=unix://" + socket, "LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST=" + strings.Repeat("a", 64),
		"LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256=" + strings.Repeat("b", 64),
	}
	var producer *exec.Cmd
	var done chan error
	stop := func() {
		if producer != nil {
			_ = producer.Process.Kill()
			<-done
			producer = nil
			_ = os.Remove(b0producer.SocketPath)
		}
	}
	control := b0producer.NewClient()
	start := func() {
		t.Helper()
		producer = exec.Command(producerBinary, "producer")
		producer.Env = producerEnv
		producer.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 10001, Gid: 10001, NoSetGroups: true}}
		if err := producer.Start(); err != nil {
			producer = nil
			t.Fatal("actual R producer failed to start")
		}
		done = make(chan error, 1)
		go func(command *exec.Cmd) { done <- command.Wait() }(producer)
		deadline := time.Now().Add(6 * time.Second)
		for {
			if _, err := control.Manifest(ctx, "c1"); err == nil {
				return
			}
			if time.Now().After(deadline) {
				t.Fatal("actual R producer never became ready")
			}
			time.Sleep(25 * time.Millisecond)
		}
	}
	t.Cleanup(func() { stop(); _ = os.Remove(sentinel) })
	start()
	plan, err := BuildColdB0ReactivationPlan(ctx, p.f.observer, p.f.client, control, request, p.target, receipt)
	if err != nil {
		t.Fatal("actual authenticated producer reactivation preview failed", err)
	}
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	write := func(name, body string) string {
		t.Helper()
		path := filepath.Join(t.TempDir(), name)
		if err := os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	var reversalBody string
	if err := p.f.observer.QueryRow(ctx, "SELECT payload FROM crawler_ownership_reversal WHERE reversal_sha256=$1", ordinary.Request().ReversalSHA256).Scan(&reversalBody); err != nil {
		t.Fatal(err)
	}
	env := map[string]string{
		"LOCAL_DATABASE_URL": p.f.dsn, "REDIS_URL": "unix://" + socket, "ORDINARY_OWNERSHIP_SOURCE_REVISION": source,
		"ORDINARY_COLD_ROUTING_EPOCH": strconv.FormatInt(p.plan.Epoch(), 10), "ORDINARY_COLD_RETIREMENT_EPOCH": strconv.FormatInt(r, 10),
		"ORDINARY_COLD_INTENT_FILE": write("intent.json", mustColdSpec(t, p.spec)), "ORDINARY_COLD_INTENT_SHA256": p.intent,
		"ORDINARY_COLD_PLAN_SHA256": p.plan.digest, "ORDINARY_COLD_REVERSAL_FILE": write("reversal.json", reversalBody),
		"ORDINARY_COLD_REVERSAL_SHA256":                  ordinary.Request().ReversalSHA256,
		"ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256": request.OrdinaryRestorationPlanSHA256,
		"ORDINARY_COLD_B0_TARGET_FILE":                   write("target.json", p.target.body), "ORDINARY_COLD_B0_TARGET_SHA256": p.target.digest,
		"ORDINARY_COLD_B0_LUA_FILE": write("queue.lua", p.target.lua), "ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE": write("receipt", receipt),
		"ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256": coldForwardBytesDigest(receipt),
	}
	type identity struct {
		Operation  string          `json:"operation"`
		Source     string          `json:"source_revision"`
		PlanSHA    string          `json:"b0_reactivation_plan_sha256"`
		Plan       json.RawMessage `json:"b0_reactivation_plan"`
		Phase      string          `json:"b0_reactivation_phase"`
		ReceiptSHA string          `json:"b0_reactivation_receipt_sha256"`
		Retirement int64           `json:"retirement_routing_epoch"`
	}
	command := func(operation string) *exec.Cmd {
		cmd := exec.Command(workerBinary, "--"+operation)
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=" + operation}
		for key, value := range env {
			cmd.Env = append(cmd.Env, key+"="+value)
		}
		return cmd
	}
	call := func(operation string, accepted bool) identity {
		t.Helper()
		output, err := command(operation).CombinedOutput()
		if !accepted {
			if err == nil || strings.Contains(string(output), p.f.dsn) || !strings.Contains(string(output), "ordinary cold coordinator") {
				t.Fatal("CLI admitted drift or exposed protected input", operation)
			}
			return identity{}
		}
		var result identity
		if err != nil || json.Unmarshal(output, &result) != nil || result.Operation != operation || result.Source != source || result.PlanSHA != plan.digest || string(result.Plan) != plan.body || result.Retirement != r {
			t.Fatal("CLI lost exact retained reactivation identity", operation)
		}
		return result
	}
	call("cold-b0-reactivation-plan", true)
	env["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = strings.Repeat("f", 64)
	call("cold-b0-reactivation-retain", false)
	env["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = plan.digest
	call("cold-b0-reactivation-retain", true)
	call("cold-b0-reactivation-retain", true)
	if !reflect.DeepEqual(before, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("CLI preview/retention changed canonical queues")
	}
	// Pause the real completion INSERT after actual SAVE/readback; kill the CLI
	// without any runtime test hook, then recover from its persisted queue effects.
	name := "reactivation_crash_" + strings.ReplaceAll(p.f.company, "-", "")
	quoted := pgx.Identifier{name}.Sanitize()
	if _, err := p.f.observer.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,915280); RETURN NEW; END $$"); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "CREATE TRIGGER "+quoted+" AFTER INSERT ON crawler_ownership_b0_reactivation_completion FOR EACH ROW EXECUTE FUNCTION "+quoted+"()"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON crawler_ownership_b0_reactivation_completion")
		_, _ = p.f.observer.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
	})
	lock, err := p.f.observer.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock(18273645,915280)"); err != nil {
		t.Fatal(err)
	}
	defer lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,915280)")
	applying := command("cold-b0-reactivation-apply")
	if err := applying.Start(); err != nil {
		t.Fatal("CLI apply failed to start")
	}
	applicationDone := make(chan error, 1)
	go func() { applicationDone <- applying.Wait() }()
	waited := false
	t.Cleanup(func() {
		if !waited {
			_ = applying.Process.Kill()
			<-applicationDone
		}
	})
	deadline := time.Now().Add(6 * time.Second)
	for {
		var paused bool
		if err := p.f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:ordinary-cold-coordinator:local' AND wait_event_type='Lock' AND query LIKE '%INSERT INTO crawler_ownership_b0_reactivation_completion%')").Scan(&paused); err != nil {
			t.Fatal(err)
		}
		if paused {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("actual CLI did not reach post-SAVE completion seam")
		}
		time.Sleep(10 * time.Millisecond)
	}
	transferred := forwardRedisSnapshot(t, p.f.client)
	if err := applying.Process.Kill(); err != nil {
		t.Fatal("actual reactivation SIGKILL failed")
	}
	<-applicationDone
	waited = true
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,915280)"); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "DROP TRIGGER "+quoted+" ON crawler_ownership_b0_reactivation_completion"); err != nil {
		t.Fatal(err)
	}
	state, err := InspectColdB0ReactivationApplication(ctx, p.f.observer, plan.digest, source)
	if err != nil || state.Phase() != "prepared" {
		t.Fatal("SIGKILL left a false completion receipt", err)
	}
	stop()
	restartPublicationRedisWithoutSave(t, p.f.client)
	if err := os.Chown(socket, 10001, 10001); err != nil {
		t.Fatal(err)
	}
	start()
	if !reflect.DeepEqual(transferred, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("saved reactivation queues lost after restart")
	}
	finished := call("cold-b0-reactivation-apply", true)
	if finished.Phase != "redis-reactivated" || !ownershipSHA256.MatchString(finished.ReceiptSHA) {
		t.Fatal("CLI did not complete persisted R application")
	}
	if again := call("cold-b0-reactivation-apply", true); again.ReceiptSHA != finished.ReceiptSHA {
		t.Fatal("completed CLI retry changed receipt")
	}
	for _, key := range []string{"ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", "ORDINARY_COLD_B0_LUA_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256"} {
		delete(env, key)
	}
	env["REDIS_URL"] = "unix:///private/unavailable/redis.sock"
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	if observed := call("cold-b0-reactivation-inspect", true); observed.ReceiptSHA != finished.ReceiptSHA || observed.Phase != "redis-reactivated" {
		t.Fatal("historical CLI inspection lost completion")
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(transferred, forwardRedisSnapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reversing" {
		t.Fatal("actual CLI replayed R effects or published ordinary ownership")
	}
}
