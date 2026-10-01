//go:build integration && linux

package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net"
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
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	"github.com/jackc/pgx/v5"
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
	producerEnv := []string{
		"LIGHTPANDA_B0_PRODUCER_MODE=enabled", "LIGHTPANDA_B0_PRODUCER_COHORT=c1",
		"LIGHTPANDA_B0_PRODUCER_CLIENT_UID=0", "LIGHTPANDA_B0_PRODUCER_SOCKET=" + b0producer.SocketPath,
		"LIGHTPANDA_B0_QUEUE_NAMESPACE=" + p.target.document.Namespace, "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0",
		"LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(r.RoutingEpoch, 10), "LIGHTPANDA_B0_LUA_PATH=" + luaPath,
		"REDIS_URL=unix://" + socket, "LIGHTPANDA_B0_ROLLBACK_PLAN_DIGEST=" + strings.Repeat("a", 64),
		"LIGHTPANDA_B0_SOURCE_RECEIPT_SHA256=" + strings.Repeat("b", 64),
	}
	var producer *exec.Cmd
	var done chan error
	stopProducer := func() {
		if producer == nil {
			return
		}
		_ = producer.Process.Kill()
		<-done
		producer = nil
		_ = os.Remove(b0producer.SocketPath)
	}
	startProducer := func() {
		t.Helper()
		command := exec.Command(producerBinary, "producer")
		command.Env = producerEnv
		command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 10001, Gid: 10001, NoSetGroups: true}}
		if err := command.Start(); err != nil {
			t.Fatal("installed producer fixture failed to start")
		}
		producer = command
		done = make(chan error, 1)
		go func() { done <- command.Wait() }()
	}
	startProducer()
	t.Cleanup(func() { stopProducer(); _ = os.Remove(sentinel) })
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
	type forwardIdentity struct {
		Operation     string          `json:"operation"`
		Source        string          `json:"source_revision"`
		Digest        string          `json:"b0_forward_plan_sha256"`
		Plan          json.RawMessage `json:"b0_forward_plan"`
		Phase         string          `json:"b0_forward_phase"`
		ReceiptSHA256 string          `json:"b0_forward_receipt_sha256"`
	}
	command := func(operation string) *exec.Cmd {
		cmd := exec.Command(workerBinary, "--"+operation)
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=" + operation}
		for k, v := range env {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		return cmd
	}
	call := func(operation string, accepted bool) *forwardIdentity {
		t.Helper()
		output, err := command(operation).CombinedOutput()
		if !accepted {
			if err == nil || !strings.Contains(string(output), "ordinary cold coordinator") || strings.Contains(string(output), p.f.dsn) {
				t.Fatal("protected native command admitted drift or exposed input")
			}
			return nil
		}
		var identity forwardIdentity
		if err != nil || json.Unmarshal(output, &identity) != nil || identity.Operation != operation || identity.Source != source || identity.Digest != plan.digest || string(identity.Plan) != plan.body {
			t.Fatal("installed native command lost exact retained manifest", operation)
		}
		return &identity
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

	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	env["REDIS_URL"] = "unix://" + socket
	env["ORDINARY_COLD_B0_TARGET_FILE"], env["ORDINARY_COLD_B0_TARGET_SHA256"], env["ORDINARY_COLD_B0_LUA_FILE"] = write("target.json", p.target.body), p.target.digest, write("queue.lua", p.target.lua)
	// Send a genuine approved activation to the actual fixed peer, then close
	// before reading any reply. Its committed effect must be recovered by
	// observation; neither the client nor the planner can infer an ACK.
	var partial coldB0ForwardTask
	for _, task := range plan.document.Tasks {
		if task.PostingID == first {
			partial = task
		}
	}
	request := b0producer.Request{Version: b0producer.Protocol, Operation: "activate", PostingID: partial.PostingID, Domain: partial.Domain, NextScrapeAtMS: partial.NextScrapeAtMS, Config: partial.Config, Browser: true, FirstTime: partial.FirstTime, OperatorTransfer: true, ExpectedDigest: partial.PreparationDigest, LegacyScheduleScore: partial.LegacyScheduleScore}
	encoded, err := b0producer.EncodeRequest(request)
	if err != nil {
		t.Fatal(err)
	}
	frame, err := framing.EncodeRecord(encoded, b0producer.FrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	connection, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: b0producer.SocketPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	_ = connection.SetDeadline(time.Now().Add(3 * time.Second))
	for len(frame) > 0 {
		n, err := connection.Write(frame)
		if err != nil || n < 1 {
			t.Fatal("actual uncertain activation send failed")
		}
		frame = frame[n:]
	}
	_ = connection.Close()
	deadline = time.Now().Add(5 * time.Second)
	for p.f.client.redis.HExists(ctx, p.target.keys()[1], first).Val() == false {
		if time.Now().After(deadline) {
			t.Fatal("actual lost-reply activation never committed")
		}
		time.Sleep(10 * time.Millisecond)
	}
	partialGuard := p.f.client.redis.HGet(ctx, "lightpanda-b0:legacy-guard", first).Val()
	if p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "-save").Err() != nil {
		t.Fatal("private SAVE denial failed")
	}
	t.Cleanup(func() { _ = p.f.client.redis.Do(context.Background(), "ACL", "SETUSER", "default", "+save").Err() })
	call("cold-b0-forward-apply", false)
	if p.f.client.redis.HGet(ctx, "lightpanda-b0:legacy-guard", first).Val() != partialGuard {
		t.Fatal("uncertain completed transfer was replayed/rounded")
	}
	if p.f.client.redis.Do(ctx, "ACL", "SETUSER", "default", "+save").Err() != nil {
		t.Fatal("private SAVE restore failed")
	}
	fullyTransferred := forwardRedisSnapshot(t, p.f.client)
	// Kill the actual native executable after SAVE/readback while an AFTER
	// INSERT trigger pauses its uncommitted completion receipt. No test hook
	// exists in the production application.
	name := "forward_crash_" + strings.ReplaceAll(p.f.company, "-", "")
	quoted := pgx.Identifier{name}.Sanitize()
	if _, err := p.f.observer.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,915279); RETURN NEW; END $$"); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "CREATE TRIGGER "+quoted+" AFTER INSERT ON crawler_ownership_b0_forward_completion FOR EACH ROW EXECUTE FUNCTION "+quoted+"()"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON crawler_ownership_b0_forward_completion")
		_, _ = p.f.observer.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
	})
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock(18273645,915279)"); err != nil {
		t.Fatal(err)
	}
	defer lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,915279)")
	applying := command("cold-b0-forward-apply")
	if err := applying.Start(); err != nil {
		t.Fatal("actual forward executable failed to start")
	}
	applicationDone := make(chan error, 1)
	go func() { applicationDone <- applying.Wait() }()
	applicationWaited := false
	t.Cleanup(func() {
		if !applicationWaited {
			_ = applying.Process.Kill()
			<-applicationDone
		}
	})
	deadline = time.Now().Add(5 * time.Second)
	for {
		var paused bool
		if err := p.f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:ordinary-cold-coordinator:local' AND wait_event_type='Lock' AND query LIKE '%INSERT INTO public.crawler_ownership_b0_forward_completion%')").Scan(&paused); err != nil {
			t.Fatal(err)
		}
		if paused {
			break
		}
		if time.Now().After(deadline) {
			_ = applying.Process.Kill()
			<-applicationDone
			applicationWaited = true
			t.Fatal("actual executable did not reach post-SAVE receipt seam")
		}
		time.Sleep(10 * time.Millisecond)
	}
	inspectEnv := map[string]string{}
	for k, v := range env {
		inspectEnv[k] = v
	}
	delete(env, "ORDINARY_COLD_B0_TARGET_FILE")
	delete(env, "ORDINARY_COLD_B0_TARGET_SHA256")
	delete(env, "ORDINARY_COLD_B0_LUA_FILE")
	env["REDIS_URL"] = "unix:///private/unavailable/redis.sock"
	if state := call("cold-b0-forward-inspect", true); state.Phase != "prepared" || state.ReceiptSHA256 != "" {
		t.Fatal("uncommitted SAVE was claimed as completed")
	}
	if applying.Process.Kill() != nil {
		t.Fatal("actual SIGKILL failed")
	}
	<-applicationDone
	applicationWaited = true
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock(18273645,915279)"); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "DROP TRIGGER "+quoted+" ON crawler_ownership_b0_forward_completion"); err != nil {
		t.Fatal(err)
	}
	// Reload the actually saved RDB and restart the actual producer at its
	// same fsynced sentinel/route, rather than reconstructing either authority.
	stopProducer()
	restartPublicationRedisWithoutSave(t, p.f.client)
	if err := os.Chown(socket, 10001, 10001); err != nil {
		t.Fatal(err)
	}
	startProducer()
	deadline = time.Now().Add(6 * time.Second)
	for {
		if _, err := control.Manifest(ctx, "c1"); err == nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("saved producer authority did not restart")
		}
		time.Sleep(25 * time.Millisecond)
	}
	if !reflect.DeepEqual(fullyTransferred, forwardRedisSnapshot(t, p.f.client)) {
		t.Fatal("RDB reload lost full transferred state")
	}
	env = inspectEnv
	finished := call("cold-b0-forward-apply", true)
	if finished.Phase != "redis-transferred" || !ownershipSHA256.MatchString(finished.ReceiptSHA256) {
		t.Fatal("exact recovery failed durable completion")
	}
	if repeated := call("cold-b0-forward-apply", true); repeated.ReceiptSHA256 != finished.ReceiptSHA256 {
		t.Fatal("completed retry changed immutable evidence")
	}
	if !reflect.DeepEqual(fullyTransferred, forwardRedisSnapshot(t, p.f.client)) || p.f.client.redis.HGet(ctx, "lightpanda-b0:legacy-guard", first).Val() != partialGuard || canonical != coldB0CanonicalSnapshot(t, p) || publicationPhase(t, p) != "reserved" {
		t.Fatal("exact native recovery replayed/selected authority")
	}
	delete(env, "ORDINARY_COLD_B0_TARGET_FILE")
	delete(env, "ORDINARY_COLD_B0_TARGET_SHA256")
	delete(env, "ORDINARY_COLD_B0_LUA_FILE")
	env["REDIS_URL"] = "unix:///private/unavailable/redis.sock"
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	if observed := call("cold-b0-forward-inspect", true); observed.Phase != "redis-transferred" || observed.ReceiptSHA256 != finished.ReceiptSHA256 {
		t.Fatal("read-only completion inspection lost receipt")
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	env["REDIS_URL"] = "unix://" + socket
	env["ORDINARY_COLD_B0_TARGET_FILE"], env["ORDINARY_COLD_B0_TARGET_SHA256"], env["ORDINARY_COLD_B0_LUA_FILE"] = write("target.json", p.target.body), p.target.digest, write("queue.lua", p.target.lua)
	env["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = strings.Repeat("f", 64)
	call("cold-forward-prepare", false)
	if publicationPhase(t, p) != "reserved" {
		t.Fatal("wrong completion selected publication")
	}
	env["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = finished.ReceiptSHA256
	for _, operation := range []string{"cold-forward-prepare", "cold-forward-publish", "cold-forward-activate", "cold-forward-activate"} {
		if observed := call(operation, true); observed.ReceiptSHA256 != finished.ReceiptSHA256 {
			t.Fatal("joint publication lost exact durable transfer identity")
		}
	}
	publishedSnapshot := forwardRedisSnapshot(t, p.f.client)
	delete(publishedSnapshot, ownershipProjectionKey)
	delete(publishedSnapshot, coldPublicationKey)
	if publicationPhase(t, p) != "active" || !reflect.DeepEqual(fullyTransferred, publishedSnapshot) || canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("completed native publication changed transferred tasks/canonical rows")
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
