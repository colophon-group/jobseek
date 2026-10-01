package worker

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
)

func TestRealNativeExecutableColdPublicationSIGKILLRecoversExactIntent(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	e := newNativeExecutableFixture(t, f, f.dsn)
	source := ordinaryFixtureSourceRevision(t)
	// Initial cold ownership preparation is confined to the validated private
	// database. The executable itself owns every subsequent joint transition.
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'"); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active").Err(); err != nil {
		t.Fatal(err)
	}
	var previous int64
	if err := f.pg.QueryRow(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')").Scan(&previous); err != nil {
		t.Fatal(err)
	}
	staging, err := queue.OpenAuthority(ctx, f.dsn, f.client, previous)
	if err != nil {
		t.Fatal(err)
	}
	prepared, err := staging.StageGreenhouseOwnership(ctx, source, []string{f.board})
	staging.Close()
	if err != nil {
		t.Fatal(err)
	}
	b0 := fixtureID(t)
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser)
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{}',60,24,'',false,true)`, b0, f.company); err != nil {
		t.Fatal(err)
	}
	if err := f.r.HSet(ctx, "board:"+b0, map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": "{}", "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}).Err(); err != nil {
		t.Fatal(err)
	}
	lua, err := os.ReadFile("../../src/lua/lightpanda_b0_queue.lua")
	if err != nil {
		t.Fatal(err)
	}
	luaFile := filepath.Join(e.directory, "b0.lua")
	if err := os.WriteFile(luaFile, lua, 0o600); err != nil {
		t.Fatal(err)
	}
	base := map[string]string{"PATH": os.Getenv("PATH"), "LOCAL_DATABASE_URL": f.dsn, "REDIS_URL": "unix://" + f.r.Options().Addr, "ORDINARY_OWNERSHIP_SOURCE_REVISION": source, "ORDINARY_COLD_ROUTING_EPOCH": fmt.Sprint(previous)}
	command := func(op string, env map[string]string) *exec.Cmd {
		t.Helper()
		cmd := exec.Command(e.binary, "--"+op)
		merged := map[string]string{}
		for k, v := range base {
			merged[k] = v
		}
		merged["ORDINARY_GO_WORKER_MODE"] = op
		for k, v := range env {
			merged[k] = v
		}
		for k, v := range merged {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		return cmd
	}
	call := func(op string, env map[string]string, accepted bool) *ColdAdminIdentity {
		t.Helper()
		output, err := command(op, env).CombinedOutput()
		if !accepted {
			if err == nil || !strings.Contains(string(output), "ordinary cold coordinator") || strings.Contains(string(output), f.dsn) || strings.Contains(string(output), f.board) {
				t.Fatal("cold command accepted invalid input or exposed protected data")
			}
			return nil
		}
		var identity ColdAdminIdentity
		if err != nil || json.Unmarshal(output, &identity) != nil || identity.SourceRevision != source || identity.Operation != op || identity.Version != "jobseek.ordinary.cold-identity/v1" {
			t.Fatalf("native cold command failed or lost bounded identity: %s", op)
		}
		return &identity
	}
	targetEnv := map[string]string{"ORDINARY_COLD_B0_LUA_FILE": luaFile, "ORDINARY_COLD_B0_NAMESPACE": "ordinary-executable-joint", "ORDINARY_COLD_B0_SHARD_ID": "lightpanda-b0", "ORDINARY_COLD_B0_COHORT": "c1"}
	target := call("cold-b0-target", targetEnv, true)
	if repeat := call("cold-b0-target", targetEnv, true); !reflect.DeepEqual(target, repeat) {
		t.Fatal("target capture changed without configuration effects")
	}
	targetFile := filepath.Join(e.directory, "target.json")
	if err := os.WriteFile(targetFile, target.Target, 0o600); err != nil {
		t.Fatal(err)
	}
	spec := queue.ColdTransitionSpec{Version: "jobseek.crawler.cold-transition/v1", TransitionID: fixtureID(t), SourceRevision: source, PreviousEpoch: previous, PreparedPlanSHA256: prepared.SHA256(), TargetB0ManifestSHA256: target.B0TargetSHA256, ActiveReleaseSHA256: strings.Repeat("3", 64), TargetReleaseSHA256: strings.Repeat("4", 64), RollbackReleaseSHA256: strings.Repeat("3", 64), ColdAttestationSHA256: strings.Repeat("5", 64)}
	body, _ := json.Marshal(spec)
	hash := sha256.Sum256(body)
	intent := hex.EncodeToString(hash[:])
	intentFile := filepath.Join(e.directory, "intent.json")
	if err := os.WriteFile(intentFile, body, 0o600); err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"ORDINARY_COLD_INTENT_FILE": intentFile, "ORDINARY_COLD_INTENT_SHA256": intent, "ORDINARY_COLD_B0_TARGET_FILE": targetFile, "ORDINARY_COLD_B0_TARGET_SHA256": target.B0TargetSHA256, "ORDINARY_COLD_B0_LUA_FILE": luaFile}
	copyEnv := func() map[string]string {
		out := map[string]string{}
		for k, v := range env {
			out[k] = v
		}
		return out
	}
	invalid := copyEnv()
	invalid["ORDINARY_OWNERSHIP_SOURCE_REVISION"] = strings.Repeat("f", 40)
	call("cold-begin", invalid, false)
	invalid = copyEnv()
	invalid["ORDINARY_GO_WORKER_MODE"] = "enabled"
	call("cold-begin", invalid, false)
	link := filepath.Join(e.directory, "intent-link.json")
	if err := os.Symlink(intentFile, link); err != nil {
		t.Fatal(err)
	}
	invalid = copyEnv()
	invalid["ORDINARY_COLD_INTENT_FILE"] = link
	call("cold-begin", invalid, false)
	// Even a matching hash cannot admit duplicate/unknown/noncanonical fields.
	for _, altered := range []string{string(body) + " ", strings.TrimSuffix(string(body), "}") + `,"version":"jobseek.crawler.cold-transition/v1"}`, strings.TrimSuffix(string(body), "}") + `,"unknown":"secret-input"}`} {
		if err := os.WriteFile(intentFile, []byte(altered), 0o600); err != nil {
			t.Fatal(err)
		}
		changed := sha256.Sum256([]byte(altered))
		invalid = copyEnv()
		invalid["ORDINARY_COLD_INTENT_SHA256"] = hex.EncodeToString(changed[:])
		call("cold-begin", invalid, false)
	}
	if err := os.WriteFile(intentFile, body, 0o600); err != nil {
		t.Fatal(err)
	}
	if got := call("cold-begin", env, true); got.IntentSHA256 != intent {
		t.Fatal("intent identity changed")
	}
	call("cold-begin", env, true)
	// Retained production intent history is never deleted. Only this owned test
	// DB can truncate fixtures, after retiring the activated test plan.
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'")
		if _, err := f.pg.Exec(context.Background(), "TRUNCATE crawler_ownership_transition"); err != nil {
			t.Error("private journal cleanup failed")
		}
	})
	reserveEnv := map[string]string{"ORDINARY_COLD_INTENT_FILE": intentFile, "ORDINARY_COLD_INTENT_SHA256": intent}
	reserved := call("cold-reserve", reserveEnv, true)
	if reserved.RoutingEpoch <= previous || reserved.Members != 1 || !planPattern.MatchString(reserved.PlanSHA256) {
		t.Fatal("reservation did not allocate exact fresh ownership")
	}
	if repeat := call("cold-reserve", reserveEnv, true); !reflect.DeepEqual(repeat, reserved) {
		t.Fatal("reservation retry allocated or changed identity")
	}
	seedExecutableColdB0(t, f, b0, reserved.RoutingEpoch, lua)
	env["ORDINARY_COLD_ROUTING_EPOCH"] = fmt.Sprint(reserved.RoutingEpoch)
	env["ORDINARY_COLD_PLAN_SHA256"] = reserved.PlanSHA256
	invalid = copyEnv()
	invalid["ORDINARY_COLD_ROUTING_EPOCH"] = fmt.Sprint(previous)
	call("cold-publish", invalid, false)
	invalid = copyEnv()
	invalid["ORDINARY_COLD_PLAN_SHA256"] = strings.Repeat("f", 64)
	call("cold-publish", invalid, false)
	call("cold-activate", env, false)
	before := coldExecutableRedisSnapshot(t, f)
	canonical := coldExecutableCanonicalSnapshot(t, f)
	call("cold-prepare", env, true)
	if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 {
		t.Fatal("prepare selected ordinary projection")
	}
	// Pause the actual executable AFTER its MSET+SAVE+readback, before PostgreSQL
	// publication commit, using a trigger in the isolated DB. No runtime test hook.
	name := "ordinary_publish_crash_" + strings.ReplaceAll(f.board, "-", "")
	quoted := pgx.Identifier{name}.Sanitize()
	const lockClass, lockObject = 18273645, 914274
	if _, err := f.pg.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,914274); RETURN NEW; END $$"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON crawler_ownership_transition")
		_, _ = f.pg.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
	})
	if _, err := f.pg.Exec(ctx, "CREATE TRIGGER "+quoted+" AFTER UPDATE ON crawler_ownership_transition FOR EACH ROW WHEN (OLD.phase='publishing' AND NEW.phase='published' AND NEW.intent_sha256='"+intent+"') EXECUTE FUNCTION "+quoted+"()"); err != nil {
		t.Fatal(err)
	}
	lock, err := f.pg.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err = lock.Exec(ctx, "SELECT pg_advisory_lock($1,$2)", lockClass, lockObject); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_, _ = lock.Exec(context.Background(), "SELECT pg_advisory_unlock($1,$2)", lockClass, lockObject)
	}()
	cmd := command("cold-publish", env)
	log, err := os.OpenFile(filepath.Join(e.directory, "cold-crash.log"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	cmd.Stdout, cmd.Stderr = log, log
	if cmd.Start() != nil {
		t.Fatal("native coordinator startup failed")
	}
	p := &nativeFixtureProcess{command: cmd, done: make(chan error, 1)}
	go func() { p.done <- cmd.Wait() }()
	t.Cleanup(func() {
		if !p.finished {
			_ = cmd.Process.Kill()
			<-p.done
		}
	})
	waitNativeFixture(t, p, "post-SAVE publication barrier", func() bool {
		var waiting bool
		_ = f.pg.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=$1 AND objid=$2 AND NOT granted)", lockClass, lockObject).Scan(&waiting)
		return waiting
	})
	var phase string
	if err := f.pg.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase); err != nil || phase != "publishing" {
		t.Fatal("uncommitted publication lost retained publishing phase")
	}
	if f.r.Get(ctx, "ordinary:ownership:active").Val() == "" || !strings.Contains(f.r.Get(ctx, "crawler:ownership:transition").Val(), `"state":"published"`) {
		t.Fatal("publication seam lacks exact Redis witness")
	}
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	crash := p.wait(t)
	var exit *exec.ExitError
	if !errors.As(crash, &exit) {
		t.Fatal("coordinator did not exit abnormally")
	}
	if status, ok := exit.Sys().(syscall.WaitStatus); !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatal("coordinator did not receive SIGKILL")
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1,$2)", lockClass, lockObject); err != nil {
		t.Fatal(err)
	}
	// Wait for the killed connection to roll back without blocking the observer.
	deadline := time.Now().Add(3 * time.Second)
	for {
		var waiting bool
		_ = f.pg.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=$1 AND objid=$2 AND NOT granted)", lockClass, lockObject).Scan(&waiting)
		if !waiting {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("killed coordinator retained transaction")
		}
		time.Sleep(10 * time.Millisecond)
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1,$2)", lockClass, lockObject); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.Exec(ctx, "DROP TRIGGER "+quoted+" ON crawler_ownership_transition"); err != nil {
		t.Fatal(err)
	}
	var planState string
	if err := f.pg.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase); err != nil || phase != "publishing" {
		t.Fatal("SIGKILL did not roll back uncommitted publication phase")
	}
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", reserved.PlanSHA256).Scan(&planState); err != nil || planState != "staged" {
		t.Fatal("SIGKILL selected ordinary database ownership")
	}
	for i := 0; i < 2; i++ {
		got := call("cold-publish", env, true)
		if got.PlanSHA256 != reserved.PlanSHA256 || got.RoutingEpoch != reserved.RoutingEpoch || got.ProjectionSHA1 != reserved.ProjectionSHA1 {
			t.Fatal("restart adopted different authority")
		}
	}
	for i := 0; i < 2; i++ {
		call("cold-activate", env, true)
	}
	if err := f.pg.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", intent).Scan(&phase); err != nil || phase != "active" {
		t.Fatal("exact recovered joint generation not active")
	}
	if !reflect.DeepEqual(before, coldExecutableRedisSnapshot(t, f)) || canonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("coordinator replayed queue/canonical effects or lost due/receipt data")
	}
	// A missing published witness cannot be repaired by restarting the one-shot.
	if err := f.r.Del(ctx, "crawler:ownership:transition").Err(); err != nil {
		t.Fatal(err)
	}
	call("cold-publish", env, false)
	call("cold-activate", env, false)
	if f.r.Exists(ctx, "crawler:ownership:transition").Val() != 0 {
		t.Fatal("coordinator reconstructed lost witness")
	}
}

func seedExecutableColdB0(t *testing.T, f nativePipelineFixture, board string, epoch int64, lua []byte) {
	t.Helper()
	ctx := context.Background()
	namespace := "ordinary-executable-joint"
	keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		keys = append(keys, "lightpanda-b0:{"+namespace+"}:"+suffix)
	}
	args := []any{"initialize_producer", "lightpanda-b0", fmt.Sprint(epoch), "go", "", "0", "", "0", "0", "0", "", "", "", "64", "2.0", "", "0", namespace, "", "0", "c1", 1, "0", "browser-use-careers"}
	if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual B0 producer initialization failed")
	}
	data, err := os.ReadFile("../../contracts/v1/b0task/testdata/python_tasks.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []struct {
			Payload string `json:"payload"`
		} `json:"cases"`
	}
	if json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) == 0 {
		t.Fatal("actual B0 task capture missing")
	}
	var envelope map[string]any
	if json.Unmarshal([]byte(corpus.Cases[0].Payload), &envelope) != nil {
		t.Fatal("B0 task codec failed")
	}
	envelope["task_id"], envelope["board_id"], envelope["routing_epoch"], envelope["shard_id"] = fixtureID(t), board, epoch, "lightpanda-b0"
	payload, _ := json.Marshal(envelope)
	hash := sha256.Sum256(payload)
	legacy := sha1.Sum(payload)
	config, _ := json.Marshal(map[string]string{"domain": "jobs.example.test", "board_id": board, "source_url": "https://jobs.example.test/posting", "description_r2_hash": "", "scrape_step": "0", "scrape_interval_hours": "24"})
	args[0], args[4], args[5], args[10], args[11], args[12], args[18], args[22] = "activate_legacy", envelope["task_id"], "3", string(payload), hex.EncodeToString(hash[:]), hex.EncodeToString(legacy[:]), string(config), "1"
	if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual B0 transfer failed")
	}
}

func coldExecutableRedisSnapshot(t *testing.T, f nativePipelineFixture) map[string]string {
	t.Helper()
	ctx := context.Background()
	keys, err := f.r.Keys(ctx, "*").Result()
	if err != nil {
		t.Fatal(err)
	}
	snapshot := map[string]string{}
	for _, key := range keys {
		if key == "ordinary:ownership:active" || key == "crawler:ownership:transition" {
			continue
		}
		body, err := f.r.Dump(ctx, key).Result()
		if err != nil {
			t.Fatal(err)
		}
		snapshot[key] = body
	}
	return snapshot
}
func coldExecutableCanonicalSnapshot(t *testing.T, f nativePipelineFixture) string {
	t.Helper()
	var body string
	if err := f.pg.QueryRow(context.Background(), `SELECT jsonb_build_object(
 'boards',(SELECT jsonb_agg(to_jsonb(b) ORDER BY id) FROM job_board b WHERE company_id=$1::uuid),
 'postings',(SELECT jsonb_agg(to_jsonb(p) ORDER BY id) FROM job_posting p WHERE company_id=$1::uuid),
 'descriptions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY posting_id,locale) FROM descriptions d WHERE posting_id IN (SELECT id FROM job_posting WHERE company_id=$1::uuid)),
 'receipts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY task_kind,task_id) FROM ordinary_worker_write_fence f WHERE board_id IN (SELECT id FROM job_board WHERE company_id=$1::uuid)))::text`, f.company).Scan(&body); err != nil {
		t.Fatal("canonical snapshot unavailable")
	}
	return body
}
