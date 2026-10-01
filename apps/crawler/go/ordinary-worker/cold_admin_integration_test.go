package worker

import (
	"context"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
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
	"github.com/redis/go-redis/v9"
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
 VALUES($1::uuid,$2::uuid,'browser-use-careers','https://jobs.example.test/careers','api_sniffer','{"precise":9007199254740993,"small":0.0000000001,"zero":0}',60,24,'',false,true)`, b0, f.company); err != nil {
		t.Fatal(err)
	}
	if err := f.r.HSet(ctx, "board:"+b0, map[string]string{"board_slug": "browser-use-careers", "board_url": "https://jobs.example.test/careers", "crawler_type": "api_sniffer", "company_id": f.company, "metadata": `{"zero":-0.00,"small":1E-10,"precise":9007199254740993.000}`, "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": "", "domain": "jobs.example.test", "monitor_needs_browser": "0", "scraper_needs_browser": "1"}).Err(); err != nil {
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
	runLegacyJointProbe(t, f, source, "", "", "", "", false)
	// Retained production intent history is never deleted. Only this owned test
	// DB can truncate fixtures, after retiring the activated test plan.
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active'")
		if _, err := f.pg.Exec(context.Background(), "TRUNCATE crawler_ownership_b0_restoration,crawler_ownership_reversal,crawler_ownership_transition"); err != nil {
			t.Error("private journal cleanup failed")
		}
		if _, err := f.pg.Exec(context.Background(), "TRUNCATE crawler_ownership_b0_restoration,crawler_ownership_b0_target"); err != nil {
			t.Error("private target cleanup failed")
		}
	})
	reserveEnv := map[string]string{"ORDINARY_COLD_INTENT_FILE": intentFile, "ORDINARY_COLD_INTENT_SHA256": intent}
	if retained := call("cold-inspect", reserveEnv, true); retained.RetainedPhase != "pending" || retained.RoutingEpoch != 0 || retained.IntentSHA256 != intent {
		t.Fatal("pending inspection adopted an allocator epoch")
	}
	reserved := call("cold-reserve", reserveEnv, true)
	if reserved.RoutingEpoch <= previous || reserved.Members != 1 || !planPattern.MatchString(reserved.PlanSHA256) {
		t.Fatal("reservation did not allocate exact fresh ownership")
	}
	if repeat := call("cold-reserve", reserveEnv, true); !reflect.DeepEqual(repeat, reserved) {
		t.Fatal("reservation retry allocated or changed identity")
	}
	if retained := call("cold-inspect", reserveEnv, true); retained.RetainedPhase != "reserved" || retained.RoutingEpoch != reserved.RoutingEpoch || retained.PlanSHA256 != reserved.PlanSHA256 {
		t.Fatal("retained inspection lost exact reservation")
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
	if retained := call("cold-inspect", reserveEnv, true); retained.RetainedPhase != "publishing" || retained.RoutingEpoch != reserved.RoutingEpoch || retained.PlanSHA256 != reserved.PlanSHA256 {
		t.Fatal("killed coordinator cannot inspect its exact retained journal")
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
	// Install the exact joint plan in the actual worker. Future scheduled work
	// keeps this startup/authority fixture independent of public network fetch.
	var due time.Time
	if err := f.pg.QueryRow(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid RETURNING next_check_at", f.board).Scan(&due); err != nil {
		t.Fatal(err)
	}
	for key := range map[string]bool{"monitors_simple:greenhouse": true, "ready:simple:1": true} {
		member := f.board
		if key == "ready:simple:1" {
			member = "greenhouse"
		}
		if err := f.r.ZAdd(ctx, key, redis.Z{Member: member, Score: float64(due.UnixMicro()) / 1e6}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	runtimeEnv := map[string]string{}
	for _, item := range e.env {
		pair := strings.SplitN(item, "=", 2)
		runtimeEnv[pair[0]] = pair[1]
	}
	runtimeEnv["LOCAL_DATABASE_URL"] = privatePipelineReferenceDSN(t, f)
	runtimeEnv["ORDINARY_OWNERSHIP_PLAN_SHA256"] = reserved.PlanSHA256
	runtimeEnv["ORDINARY_OWNERSHIP_PROJECTION_SHA1"] = reserved.ProjectionSHA1
	runtimeEnv["ORDINARY_OWNERSHIP_ROUTING_EPOCH"] = fmt.Sprint(reserved.RoutingEpoch)
	workerEnv := func(luaPath string) []string {
		out := []string{}
		for k, v := range runtimeEnv {
			out = append(out, k+"="+v)
		}
		if luaPath != "" {
			out = append(out, "ORDINARY_GO_B0_AUDIT_LUA_FILE="+luaPath)
		}
		return out
	}
	rejectStartup := func(path string) {
		t.Helper()
		bad := exec.Command(e.binary)
		bad.Env = workerEnv(path)
		if output, err := bad.CombinedOutput(); err == nil || !strings.Contains(string(output), "ordinary worker failed") || strings.Contains(string(output), f.dsn) {
			t.Fatal("joint worker admitted absent/untrusted audit or exposed input")
		}
	}
	baseline := coldExecutableRedisSnapshot(t, f)
	canonical = coldExecutableCanonicalSnapshot(t, f)
	legacyProbe := func(accepted bool) {
		runLegacyJointProbe(t, f, source, reserved.PlanSHA256, reserved.ProjectionSHA1, fmt.Sprint(reserved.RoutingEpoch), luaFile, accepted)
	}
	legacyProbe(true)
	// Same native-published journal/target and actual source-pinned audit, with
	// reversible faults confined to the owned fixture. No production repair API.
	for _, mode := range []string{"marker", "projection", "route_epoch", "route_ttl", "producer_cohort", "selector", "record", "canonical_disabled", "redis_config"} {
		route := "lightpanda-b0:{ordinary-executable-joint}:route"
		marker := f.r.Get(ctx, "crawler:ownership:transition").Val()
		projection := f.r.Get(ctx, "ordinary:ownership:active").Val()
		switch mode {
		case "marker":
			err = f.r.Del(ctx, "crawler:ownership:transition").Err()
		case "projection":
			err = f.r.Set(ctx, "ordinary:ownership:active", projection+" ", 0).Err()
		case "route_epoch":
			err = f.r.HSet(ctx, route, "routing_epoch", reserved.RoutingEpoch-1).Err()
		case "route_ttl":
			err = f.r.Expire(ctx, route, time.Hour).Err()
		case "producer_cohort":
			err = f.r.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "c2").Err()
		case "selector":
			err = f.r.HDel(ctx, "lightpanda-b0:producer-owner", "board_slug:browser-use-careers").Err()
		case "record":
			err = f.r.HSet(ctx, "lightpanda-b0:{ordinary-executable-joint}:records", "not-a-task", "{}").Err()
		case "canonical_disabled":
			_, err = f.pg.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", b0)
		case "redis_config":
			err = f.r.HSet(ctx, "board:"+b0, "check_interval_minutes", "61").Err()
		}
		if err != nil {
			t.Fatal("private legacy authority fault failed")
		}
		faultState := fullColdExecutableRedisSnapshot(t, f)
		faultCanonical := coldExecutableCanonicalSnapshot(t, f)
		legacyProbe(false)
		if !reflect.DeepEqual(faultState, fullColdExecutableRedisSnapshot(t, f)) || faultCanonical != coldExecutableCanonicalSnapshot(t, f) {
			t.Fatal("rejected legacy owner changed private queue or canonical state: " + mode)
		}
		switch mode {
		case "marker":
			err = f.r.Set(ctx, "crawler:ownership:transition", marker, 0).Err()
		case "projection":
			err = f.r.Set(ctx, "ordinary:ownership:active", projection, 0).Err()
		case "route_epoch":
			err = f.r.HSet(ctx, route, "routing_epoch", reserved.RoutingEpoch).Err()
		case "route_ttl":
			err = f.r.Persist(ctx, route).Err()
		case "producer_cohort":
			err = f.r.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "c1").Err()
		case "selector":
			err = f.r.HSet(ctx, "lightpanda-b0:producer-owner", "board_slug:browser-use-careers", "1").Err()
		case "record":
			err = f.r.HDel(ctx, "lightpanda-b0:{ordinary-executable-joint}:records", "not-a-task").Err()
		case "canonical_disabled":
			_, err = f.pg.Exec(ctx, "UPDATE job_board SET is_enabled=true WHERE id=$1::uuid", b0)
		case "redis_config":
			err = f.r.HSet(ctx, "board:"+b0, "check_interval_minutes", "60").Err()
		}
		if err != nil {
			t.Fatal("owned fixture fault cleanup failed")
		}
	}
	legacyProbe(true)
	// Runtime admission accepts a conserved real B0 inflight lease. Publication
	// quiescence conditions must not prevent unrelated ordinary claims at runtime.
	b0Keys := []string{}
	for _, suffix := range []string{"route", "records", "ready", "inflight", "dead", "terminal", "origin-holders"} {
		b0Keys = append(b0Keys, "lightpanda-b0:{ordinary-executable-joint}:"+suffix)
	}
	now, err := f.r.Time(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	b0Args := []any{"claim_next", "lightpanda-b0", fmt.Sprint(reserved.RoutingEpoch), "go", "", "0", fmt.Sprint(now.UnixMilli()), "600000", "0", "0", "", "", "", "64", "2.0", "", "0", "ordinary-executable-joint", "", "0", "c1", 1, "0", "browser-use-careers"}
	b0Claim, err := f.r.Eval(ctx, string(lua), b0Keys, b0Args...).Slice()
	if err != nil || len(b0Claim) != 12 || b0Claim[0] != "accepted" || f.r.ZCard(ctx, b0Keys[3]).Val() != 1 {
		t.Fatal("actual legacy runtime B0 inflight fixture failed")
	}
	if _, err := f.pg.Exec(ctx, "SELECT public.jobseek_lightpanda_b0_activate_write_fence($1::uuid,$2,$3,'go',$4,$5,$6)", b0Claim[3], "lightpanda-b0", reserved.RoutingEpoch, int64(3), b0Claim[7], b0Claim[4]); err != nil {
		t.Fatal("actual native B0 fence unavailable", err)
	}
	legacyProbe(true)
	baseline = coldExecutableRedisSnapshot(t, f)
	rejectStartup("")
	badLua := filepath.Join(e.directory, "untrusted.lua")
	if err := os.WriteFile(badLua, append(append([]byte{}, lua...), '\n'), 0o600); err != nil {
		t.Fatal(err)
	}
	rejectStartup(badLua)
	auditLink := filepath.Join(e.directory, "audit-link.lua")
	if err := os.Symlink(luaFile, auditLink); err != nil {
		t.Fatal(err)
	}
	rejectStartup(auditLink)
	e.env = workerEnv(luaFile)
	running := e.start(t, "joint-worker")
	httpClient := &http.Client{Timeout: time.Second}
	defer httpClient.CloseIdleConnections()
	waitNativeFixture(t, running, "active joint native worker readiness", func() bool {
		response, err := httpClient.Get("http://" + e.address + "/healthz")
		if err != nil {
			return false
		}
		defer response.Body.Close()
		return response.StatusCode == http.StatusNoContent
	})
	if !reflect.DeepEqual(baseline, coldExecutableRedisSnapshot(t, f)) || canonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("joint worker startup changed future queues or canonical state")
	}
	// A missing published witness cannot be repaired by restarting the one-shot.
	if err := f.r.Del(ctx, "crawler:ownership:transition").Err(); err != nil {
		t.Fatal(err)
	}
	if err := running.wait(t); err == nil {
		t.Fatal("live native worker kept running after joint witness loss")
	}
	legacyProbe(false)
	if !reflect.DeepEqual(baseline, coldExecutableRedisSnapshot(t, f)) || canonical != coldExecutableCanonicalSnapshot(t, f) || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("lost joint authority popped work or changed canonical/future state")
	}
	call("cold-publish", env, false)
	call("cold-activate", env, false)
	if f.r.Exists(ctx, "crawler:ownership:transition").Val() != 0 {
		t.Fatal("coordinator reconstructed lost witness")
	}
	if retained := call("cold-inspect", reserveEnv, true); retained.RetainedPhase != "active" || retained.RoutingEpoch != reserved.RoutingEpoch {
		t.Fatal("witness containment discarded retained journal history")
	}
	// Explicit cold reversal is still possible after witness loss. Host-cold and
	// release digests here are private fixture bindings, not host attestation.
	reversal := queue.ColdReversalSpec{Version: "jobseek.crawler.cold-reversal/v1", ReversalID: fixtureID(t), ForwardIntentSHA256: intent,
		SourceRevision: source, SourceEpoch: reserved.RoutingEpoch, SourcePlanSHA256: reserved.PlanSHA256, SourcePhase: "active",
		RollbackReleaseSHA256: spec.RollbackReleaseSHA256, RollbackOrdinaryPlanSHA256: spec.PreviousOrdinaryPlanSHA256,
		RollbackB0ReceiptSHA256: spec.PreviousB0ReceiptSHA256, ColdAttestationSHA256: strings.Repeat("6", 64)}
	reversalBody, _ := json.Marshal(reversal)
	reversalHash := sha256.Sum256(reversalBody)
	reversalSHA := hex.EncodeToString(reversalHash[:])
	reversalFile := filepath.Join(e.directory, "reversal.json")
	if err := os.WriteFile(reversalFile, reversalBody, 0o600); err != nil {
		t.Fatal(err)
	}
	reversalEnv := map[string]string{"ORDINARY_COLD_INTENT_FILE": intentFile, "ORDINARY_COLD_INTENT_SHA256": intent,
		"ORDINARY_COLD_ROUTING_EPOCH": fmt.Sprint(reserved.RoutingEpoch), "ORDINARY_COLD_PLAN_SHA256": reserved.PlanSHA256,
		"ORDINARY_COLD_REVERSAL_FILE": reversalFile, "ORDINARY_COLD_REVERSAL_SHA256": reversalSHA}
	for _, altered := range []string{string(reversalBody) + " ", strings.TrimSuffix(string(reversalBody), "}") + `,"source_phase":"active"}`, strings.TrimSuffix(string(reversalBody), "}") + `,"unknown":"secret-input"}`} {
		if err := os.WriteFile(reversalFile, []byte(altered), 0o600); err != nil {
			t.Fatal(err)
		}
		h := sha256.Sum256([]byte(altered))
		reversalEnv["ORDINARY_COLD_REVERSAL_SHA256"] = hex.EncodeToString(h[:])
		call("cold-reversal-begin", reversalEnv, false)
	}
	if err := os.WriteFile(reversalFile, reversalBody, 0o600); err != nil {
		t.Fatal(err)
	}
	reversalEnv["ORDINARY_COLD_REVERSAL_SHA256"] = reversalSHA
	if got := call("cold-reversal-begin", reversalEnv, true); got.ReversalSHA256 != reversalSHA {
		t.Fatal("actual executable lost exact reversal identity")
	}
	call("cold-reversal-begin", reversalEnv, true)
	if got := call("cold-reversal-inspect", reversalEnv, true); got.ReversalPhase != "pending" || got.RetirementEpoch != 0 || got.PlanSHA256 != reserved.PlanSHA256 {
		t.Fatal("pending reversal inspection adopted an epoch")
	}
	retireBaseline, retireCanonical := fullColdExecutableRedisSnapshot(t, f), coldExecutableCanonicalSnapshot(t, f)
	// Pause AFTER nextval and ordinary retirement, before the reservation commit.
	// The sequence advance survives SIGKILL, while its SQL effects roll back.
	retireName := "ordinary_retire_crash_" + strings.ReplaceAll(f.board, "-", "")
	retireQuoted := pgx.Identifier{retireName}.Sanitize()
	const retireClass, retireObject = 18273645, 914275
	if _, err := f.pg.Exec(ctx, "CREATE FUNCTION "+retireQuoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,914275); RETURN NEW; END $$"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+retireQuoted+" ON crawler_ownership_reversal")
		_, _ = f.pg.Exec(context.Background(), "DROP FUNCTION "+retireQuoted+"()")
	})
	if _, err := f.pg.Exec(ctx, "CREATE TRIGGER "+retireQuoted+" AFTER UPDATE ON crawler_ownership_reversal FOR EACH ROW WHEN (OLD.phase='pending' AND NEW.phase='reserved' AND NEW.reversal_sha256='"+reversalSHA+"') EXECUTE FUNCTION "+retireQuoted+"()"); err != nil {
		t.Fatal(err)
	}
	// The fixture has two PG connections: reuse its now-unlocked barrier
	// connection so the other remains available for bounded observation.
	retireLock := lock
	if _, err := retireLock.Exec(ctx, "SELECT pg_advisory_lock($1,$2)", retireClass, retireObject); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_, _ = retireLock.Exec(context.Background(), "SELECT pg_advisory_unlock($1,$2)", retireClass, retireObject)
	}()
	retireCommand := command("cold-reversal-reserve", reversalEnv)
	retireLog, err := os.OpenFile(filepath.Join(e.directory, "retirement-crash.log"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer retireLog.Close()
	retireCommand.Stdout, retireCommand.Stderr = retireLog, retireLog
	if err := retireCommand.Start(); err != nil {
		t.Fatal("actual retirement coordinator failed to start")
	}
	retireProcess := &nativeFixtureProcess{command: retireCommand, done: make(chan error, 1)}
	go func() { retireProcess.done <- retireCommand.Wait() }()
	t.Cleanup(func() {
		if !retireProcess.finished {
			_ = retireCommand.Process.Kill()
			<-retireProcess.done
		}
	})
	waitNativeFixture(t, retireProcess, "post-nextval retirement barrier", func() bool {
		observation, cancel := context.WithTimeout(ctx, time.Second)
		defer cancel()
		var waiting bool
		_ = f.pg.QueryRow(observation, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=$1 AND objid=$2 AND NOT granted)", retireClass, retireObject).Scan(&waiting)
		return waiting
	})
	var burned int64
	if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&burned); err != nil || burned != reserved.RoutingEpoch+1 {
		t.Fatal("retirement seam lacks a burned sequence advance")
	}
	if got := call("cold-reversal-inspect", reversalEnv, true); got.ReversalPhase != "pending" || got.RetirementEpoch != 0 {
		t.Fatal("inspection blocked behind uncommitted retirement or adopted burned epoch")
	}
	if err := retireCommand.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	crash = retireProcess.wait(t)
	if !errors.As(crash, &exit) {
		t.Fatal("retirement coordinator did not exit abnormally")
	}
	if status, ok := exit.Sys().(syscall.WaitStatus); !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatal("retirement coordinator did not receive actual SIGKILL")
	}
	if _, err := retireLock.Exec(ctx, "SELECT pg_advisory_unlock($1,$2)", retireClass, retireObject); err != nil {
		t.Fatal(err)
	}
	// This DDL waits for the killed transaction's rollback and cannot mutate
	// runtime state; statements in the owned fixture remain bounded.
	if _, err := f.pg.Exec(ctx, "DROP TRIGGER "+retireQuoted+" ON crawler_ownership_reversal"); err != nil {
		t.Fatal(err)
	}
	if got := call("cold-reversal-inspect", reversalEnv, true); got.ReversalPhase != "pending" || got.RetirementEpoch != 0 {
		t.Fatal("SIGKILL lost pending reversal or adopted burned epoch")
	}
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", reserved.PlanSHA256).Scan(&planState); err != nil || planState != "active" {
		t.Fatal("killed retirement failed to roll back ordinary owner mutation")
	}
	retired := call("cold-reversal-reserve", reversalEnv, true)
	if retired.ReversalPhase != "reserved" || retired.RetirementEpoch != burned+1 || retired.ReversalSHA256 != reversalSHA {
		t.Fatal("restarted coordinator adopted burned retirement epoch")
	}
	if repeat := call("cold-reversal-reserve", reversalEnv, true); !reflect.DeepEqual(repeat, retired) {
		t.Fatal("uncertain retirement commit retry allocated again")
	}
	if got := call("cold-reversal-inspect", reversalEnv, true); got.RetirementEpoch != retired.RetirementEpoch || got.ReversalPhase != "reserved" {
		t.Fatal("retained inspection lost exact committed retirement")
	}
	legacyProbe(false)
	if !reflect.DeepEqual(retireBaseline, fullColdExecutableRedisSnapshot(t, f)) || retireCanonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("actual retirement SIGKILL/restart replayed Redis/canonical/future-due effects")
	}
	if _, err := f.pg.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1", intent); err == nil {
		t.Fatal("retirement alone released authority before complete restoration")
	}
	request := queue.ColdB0RollbackRequest{ReversalSHA256: reversalSHA, SourceRevision: source, RetirementEpoch: retired.RetirementEpoch, B0SourceEpoch: reserved.RoutingEpoch, SourceReceiptSHA256: strings.Repeat("8", 64)}
	requestFile := filepath.Join(e.directory, "b0-restore-request.json")
	requestBody, _ := json.Marshal(request)
	requestHash := sha256.Sum256(requestBody)
	if err := os.WriteFile(requestFile, requestBody, 0o600); err != nil {
		t.Fatal(err)
	}
	restoreEnv := map[string]string{}
	for key, value := range reversalEnv {
		restoreEnv[key] = value
	}
	restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_FILE"] = requestFile
	restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"] = hex.EncodeToString(requestHash[:])
	restoreEnv["ORDINARY_COLD_B0_TARGET_FILE"], restoreEnv["ORDINARY_COLD_B0_TARGET_SHA256"], restoreEnv["ORDINARY_COLD_B0_LUA_FILE"] = targetFile, target.B0TargetSHA256, luaFile
	// Live source inflight work cannot be silently converted by restoration.
	call("cold-b0-rollback-plan", restoreEnv, false)
	completed := append([]any{}, b0Args...)
	completed[0] = "complete"
	completed[4] = b0Claim[3]
	completed[5] = b0Claim[6]
	completed[6] = b0Claim[4]
	completed[11] = b0Claim[7]
	completed[16] = b0Claim[5]
	b0Legacy := sha1.Sum([]byte(b0Claim[8].(string)))
	completed[12] = hex.EncodeToString(b0Legacy[:])
	if reply, err := f.r.Eval(ctx, string(lua), b0Keys, completed...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("cold fixture could not finish actual B0 source lease")
	}
	for _, altered := range []string{string(requestBody) + " ", strings.TrimSuffix(string(requestBody), "}") + `,"retirement_epoch":1}`, strings.TrimSuffix(string(requestBody), "}") + `,"unknown":"secret-input"}`} {
		if err := os.WriteFile(requestFile, []byte(altered), 0o600); err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256([]byte(altered))
		restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"] = hex.EncodeToString(hash[:])
		call("cold-b0-rollback-plan", restoreEnv, false)
	}
	if err := os.WriteFile(requestFile, requestBody, 0o600); err != nil {
		t.Fatal(err)
	}
	restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"] = hex.EncodeToString(requestHash[:])
	restoreBaseline, restoreCanonical := fullColdExecutableRedisSnapshot(t, f), coldExecutableCanonicalSnapshot(t, f)
	preparedRestore := call("cold-b0-rollback-plan", restoreEnv, true)
	if !planPattern.MatchString(preparedRestore.B0RollbackPlanSHA256) || len(preparedRestore.B0RollbackPlan) == 0 || preparedRestore.RetirementEpoch != retired.RetirementEpoch {
		t.Fatal("actual executable did not prepare exact canonical B0 plan")
	}
	restoreEnv["ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256"] = preparedRestore.B0RollbackPlanSHA256
	if got := call("cold-b0-rollback-retain", restoreEnv, true); got.B0RestorationPhase != "prepared" {
		t.Fatal("actual executable did not retain plan before effects")
	}
	call("cold-b0-rollback-retain", restoreEnv, true)
	inspectRestoreEnv := map[string]string{}
	for key, value := range restoreEnv {
		inspectRestoreEnv[key] = value
	}
	delete(inspectRestoreEnv, "ORDINARY_COLD_B0_TARGET_FILE")
	delete(inspectRestoreEnv, "ORDINARY_COLD_B0_TARGET_SHA256")
	delete(inspectRestoreEnv, "ORDINARY_COLD_B0_LUA_FILE")
	if got := call("cold-b0-rollback-inspect", inspectRestoreEnv, true); got.B0RestorationPhase != "prepared" || got.B0RollbackPlanSHA256 != preparedRestore.B0RollbackPlanSHA256 {
		t.Fatal("retained restoration inspection adopted authority")
	}
	if !reflect.DeepEqual(restoreBaseline, fullColdExecutableRedisSnapshot(t, f)) || restoreCanonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("preparation/retention replayed canonical/queue data")
	}
	// A correctly hashed but different protected receipt must reject before
	// any restore effect, even when the approved retained plan digest is valid.
	wrongRequest := request
	wrongRequest.SourceReceiptSHA256 = strings.Repeat("9", 64)
	wrongBody, _ := json.Marshal(wrongRequest)
	wrongHash := sha256.Sum256(wrongBody)
	if err := os.WriteFile(requestFile, wrongBody, 0o600); err != nil {
		t.Fatal(err)
	}
	restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"] = hex.EncodeToString(wrongHash[:])
	call("cold-b0-rollback-restore", restoreEnv, false)
	if !reflect.DeepEqual(restoreBaseline, fullColdExecutableRedisSnapshot(t, f)) {
		t.Fatal("wrong protected request caused restoration effects")
	}
	if err := os.WriteFile(requestFile, requestBody, 0o600); err != nil {
		t.Fatal(err)
	}
	restoreEnv["ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"] = hex.EncodeToString(requestHash[:])
	restoreName := "ordinary_b0_restore_crash_" + strings.ReplaceAll(f.board, "-", "")
	restoreQuoted := pgx.Identifier{restoreName}.Sanitize()
	const restoreClass, restoreObject = 18273645, 914276
	if _, err := f.pg.Exec(ctx, "CREATE FUNCTION "+restoreQuoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,914276); RETURN NEW; END $$"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+restoreQuoted+" ON crawler_ownership_b0_restoration")
		_, _ = f.pg.Exec(context.Background(), "DROP FUNCTION "+restoreQuoted+"()")
	})
	if _, err := f.pg.Exec(ctx, "CREATE TRIGGER "+restoreQuoted+" AFTER UPDATE ON crawler_ownership_b0_restoration FOR EACH ROW WHEN (OLD.phase='prepared' AND NEW.phase='redis-restored' AND NEW.plan_sha256='"+preparedRestore.B0RollbackPlanSHA256+"') EXECUTE FUNCTION "+restoreQuoted+"()"); err != nil {
		t.Fatal(err)
	}
	// Reuse the already owned fixture connection; keep the other free for
	// bounded MVCC inspection while the executable is paused after SAVE/readback.
	if _, err := retireLock.Exec(ctx, "SELECT pg_advisory_lock($1,$2)", restoreClass, restoreObject); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_, _ = retireLock.Exec(context.Background(), "SELECT pg_advisory_unlock($1,$2)", restoreClass, restoreObject)
	}()
	restoreCommand := command("cold-b0-rollback-restore", restoreEnv)
	restoreLog, err := os.OpenFile(filepath.Join(e.directory, "b0-restoration-crash.log"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer restoreLog.Close()
	restoreCommand.Stdout, restoreCommand.Stderr = restoreLog, restoreLog
	if err := restoreCommand.Start(); err != nil {
		t.Fatal("actual restoration coordinator failed to start")
	}
	restoreProcess := &nativeFixtureProcess{command: restoreCommand, done: make(chan error, 1)}
	go func() { restoreProcess.done <- restoreCommand.Wait() }()
	t.Cleanup(func() {
		if !restoreProcess.finished {
			_ = restoreCommand.Process.Kill()
			<-restoreProcess.done
		}
	})
	waitNativeFixture(t, restoreProcess, "post-SAVE restoration barrier", func() bool {
		observation, cancel := context.WithTimeout(ctx, time.Second)
		defer cancel()
		var waiting bool
		_ = f.pg.QueryRow(observation, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=$1 AND objid=$2 AND NOT granted)", restoreClass, restoreObject).Scan(&waiting)
		return waiting
	})
	if got := call("cold-b0-rollback-inspect", inspectRestoreEnv, true); got.B0RestorationPhase != "prepared" {
		t.Fatal("inspection blocked or adopted uncommitted restoration")
	}
	var b0Fences int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1::uuid", b0Claim[3]).Scan(&b0Fences); err != nil || b0Fences != 1 {
		t.Fatal("historical fence cleared before durable Redis progress")
	}
	if f.r.HGet(ctx, "lightpanda-b0:producer-owner", "schema").Val() != "jobseek.lightpanda.producer-rollback/v1" {
		t.Fatal("paused executable lacks actual saved restoration tombstone")
	}
	if err := restoreCommand.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	crash = restoreProcess.wait(t)
	if !errors.As(crash, &exit) {
		t.Fatal("restoration coordinator did not exit abnormally")
	}
	if status, ok := exit.Sys().(syscall.WaitStatus); !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatal("restoration did not receive actual SIGKILL")
	}
	if _, err := retireLock.Exec(ctx, "SELECT pg_advisory_unlock($1,$2)", restoreClass, restoreObject); err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.Exec(ctx, "DROP TRIGGER "+restoreQuoted+" ON crawler_ownership_b0_restoration"); err != nil {
		t.Fatal(err)
	}
	if got := call("cold-b0-rollback-inspect", inspectRestoreEnv, true); got.B0RestorationPhase != "prepared" {
		t.Fatal("SIGKILL lost retained restoration phase")
	}
	postRestoreCrash := fullColdExecutableRedisSnapshot(t, f)
	if restored := call("cold-b0-rollback-restore", restoreEnv, true); restored.B0RestorationPhase != "fences-cleared" || restored.RetirementEpoch != retired.RetirementEpoch {
		t.Fatal("actual restart failed exact tombstone recovery")
	}
	call("cold-b0-rollback-restore", restoreEnv, true)
	if !reflect.DeepEqual(postRestoreCrash, fullColdExecutableRedisSnapshot(t, f)) || restoreCanonical != coldExecutableCanonicalSnapshot(t, f) {
		t.Fatal("actual SIGKILL/restart replayed canonical/receipt/future/queue effects")
	}
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM lightpanda_b0_write_fence WHERE job_posting_id=$1::uuid", b0Claim[3]).Scan(&b0Fences); err != nil || b0Fences != 0 {
		t.Fatal("exact historical source fence not cleared")
	}
	var epochAfterRestore int64
	if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epochAfterRestore); err != nil || epochAfterRestore != retired.RetirementEpoch {
		t.Fatal("restoration allocated/adopted another epoch")
	}
	if score, err := f.r.ZScore(ctx, "scrapes_browser:jobs.example.test", b0Claim[3].(string)).Result(); err != nil || score != 1925089445.100001 {
		t.Fatal("actual restore lost canonical future B0 due")
	}
	if config := f.r.HGet(ctx, "scrape:"+b0Claim[3].(string), "description_r2_hash").Val(); config != "-9223372036854775808" {
		t.Fatal("actual restore lost canonical bigint hash")
	}
	legacyProbe(false)
	if _, err := f.pg.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1", intent); err == nil {
		t.Fatal("B0 restore alone released host/ordinary ownership")
	}
}

func runLegacyJointProbe(t *testing.T, f nativePipelineFixture, source, plan, projection, epoch, lua string, accepted bool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	result := "rejected"
	if accepted {
		result = "accepted"
	}
	cmd := exec.CommandContext(ctx, "uv", "run", "--frozen", "--no-sync", "python", "-m", "contracts.v1.tools.legacy_joint_probe", result)
	cmd.Dir = "../.."
	if plan == "" {
		source = ""
	}
	cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "LOCAL_DATABASE_URL=" + f.dsn, "REDIS_URL=unix://" + f.r.Options().Addr,
		"JOBSEEK_ORDINARY_LEGACY_JOINT_PROBE=1", "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + source,
		"ORDINARY_OWNERSHIP_PLAN_SHA256=" + plan, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + projection,
		"ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + epoch, "ORDINARY_GO_B0_AUDIT_LUA_FILE=" + lua}
	if venv := os.Getenv("UV_PROJECT_ENVIRONMENT"); venv != "" {
		cmd.Env = append(cmd.Env, "UV_PROJECT_ENVIRONMENT="+venv)
	}
	output, err := cmd.CombinedOutput()
	if err != nil || string(output) != "legacy joint probe verified\n" {
		t.Fatal("actual legacy/native joint admission probe diverged")
	}
}

func fullColdExecutableRedisSnapshot(t *testing.T, f nativePipelineFixture) map[string]string {
	t.Helper()
	ctx := context.Background()
	keys, err := f.r.Keys(ctx, "*").Result()
	if err != nil {
		t.Fatal("private full Redis observation failed")
	}
	state := map[string]string{}
	for _, key := range keys {
		body, err := f.r.Dump(ctx, key).Result()
		if err != nil {
			t.Fatal("private full Redis observation failed")
		}
		ttl, err := f.r.PTTL(ctx, key).Result()
		if err != nil {
			t.Fatal("private full Redis expiry observation failed")
		}
		// Wall-clock countdown is not drift, while expiry removal/addition is.
		state[key] = fmt.Sprint(ttl > 0) + ":" + body
	}
	return state
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
	envelope["source_url"] = "https://jobs.example.test/posting/" + envelope["task_id"].(string)
	payload, _ := json.Marshal(envelope)
	hash := sha256.Sum256(payload)
	legacy := sha1.Sum(payload)
	config, _ := json.Marshal(map[string]string{"domain": "jobs.example.test", "board_id": board, "source_url": envelope["source_url"].(string), "description_r2_hash": "", "scrape_step": "0", "scrape_interval_hours": "24"})
	args[0], args[4], args[5], args[10], args[11], args[12], args[18], args[22] = "activate_legacy", envelope["task_id"], "3", string(payload), hex.EncodeToString(hash[:]), hex.EncodeToString(legacy[:]), string(config), "1"
	if reply, err := f.r.Eval(ctx, string(lua), keys, args...).Slice(); err != nil || len(reply) != 12 || reply[0] != "accepted" {
		t.Fatal("actual B0 transfer failed")
	}
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,description_r2_hash,next_scrape_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,-9223372036854775808,'2031-01-02 03:04:05.100001+00')`, envelope["task_id"], f.company, board, envelope["source_url"]); err != nil {
		t.Fatal("canonical native B0 posting unavailable", err)
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
