//go:build integration && linux

package executor

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/url"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"google.golang.org/protobuf/proto"
)

const installedUID = uint32(10001)

type installedLog struct {
	mu   sync.Mutex
	data bytes.Buffer
}

func (l *installedLog) Write(body []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.data.Write(body)
}
func (l *installedLog) String() string { l.mu.Lock(); defer l.mu.Unlock(); return l.data.String() }

func installedCommand(binary string, environment []string, uid uint32, args ...string) *exec.Cmd {
	shellArgs := append([]string{"-c", `ulimit -n 64; exec "$@"`, "native-executor-fixture", binary}, args...)
	command := exec.Command("/bin/sh", shellArgs...)
	command.Env = environment
	command.Dir = "/tmp"
	command.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uid, Gid: uid, NoSetGroups: true}}
	return command
}

// The helper is a Go socket client with no database or renderer credentials.
// It runs at the actual peer UID instead of replacing the server's peer check.
func TestInstalledNativeClientHelper(t *testing.T) {
	mode := os.Getenv("JOBSEEK_NATIVE_CLIENT_MODE")
	if mode == "" {
		t.Skip("installed client subprocess only")
	}
	raw, err := base64.StdEncoding.DecodeString(os.Getenv("JOBSEEK_NATIVE_CLIENT_REQUEST"))
	if err != nil {
		t.Fatal(err)
	}
	conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: SocketPath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(20 * time.Second))
	var request map[string]any
	if json.Unmarshal(raw, &request) != nil {
		t.Fatal("invalid client fixture")
	}
	failure := WriteMessage(conn, request)
	var frame []byte
	if failure == nil {
		frame, failure = ReadFrame(conn)
	}
	if mode == "wrong_peer" || mode == "capacity" {
		if failure == nil {
			t.Fatal("rejected client received a task response")
		}
		_, _ = os.Stdout.Write([]byte(`{"type":"rejected"}`))
		os.Exit(0)
	}
	if failure != nil {
		t.Fatal(failure)
	}
	var authorize map[string]json.RawMessage
	if json.Unmarshal(frame, &authorize) != nil || string(authorize["type"]) != `"authorize"` {
		t.Fatal("authorization not requested")
	}
	if mode == "hold" {
		_, _ = os.Stdout.Write(append(frame, '\n'))
		if _, err := bufio.NewReader(os.Stdin).ReadBytes('\n'); err != nil {
			os.Exit(0)
		}
	}
	var claim string
	var lease int64
	if json.Unmarshal(authorize["claim_token"], &claim) != nil || json.Unmarshal(authorize["lease_until_ms"], &lease) != nil {
		t.Fatal("invalid authorization")
	}
	granted := lease + 1
	if supplied := os.Getenv("JOBSEEK_NATIVE_CLIENT_AUTHORIZED_LEASE"); supplied != "" {
		var err error
		granted, err = strconv.ParseInt(supplied, 10, 64)
		if err != nil || granted <= lease {
			t.Fatal("invalid fixture Redis authorization")
		}
	}
	if err := WriteMessage(conn, map[string]any{"type": "authorized", "claim_token": claim, "lease_until_ms": granted}); err != nil {
		t.Fatal(err)
	}
	frame, err = ReadFrame(conn)
	if err != nil {
		t.Fatal(err)
	}
	if mode == "discard_ack" {
		var response map[string]json.RawMessage
		if json.Unmarshal(frame, &response) != nil || string(response["type"]) != `"committed"` {
			t.Fatal("discarded response was not committed")
		}
		// Deliberately withhold this acknowledgement from the modeled supervisor.
		// The parent now kills the real owner and verifies restart/duplicate truth.
		_, _ = os.Stdout.Write([]byte(`{"type":"ack_discarded"}`))
		os.Exit(0)
	}
	_, _ = os.Stdout.Write(frame)
	os.Exit(0)
}

func installedClient(t *testing.T, request Request, mode string, uid uint32) *exec.Cmd {
	return installedClientAuthorized(t, request, mode, uid, 0)
}

func installedClientAuthorized(t *testing.T, request Request, mode string, uid uint32, granted int64) *exec.Cmd {
	t.Helper()
	result, err := (proto.MarshalOptions{Deterministic: true}).Marshal(request.Result)
	if err != nil {
		t.Fatal(err)
	}
	payload, err := json.Marshal(map[string]any{"version": Protocol, "task_payload": request.TaskPayload, "payload_sha256": request.PayloadSHA256, "claim_token": request.ClaimToken, "lease_until_ms": request.LeaseUntilMS, "browser_result": base64.StdEncoding.EncodeToString(result)})
	if err != nil {
		t.Fatal(err)
	}
	self, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	environment := []string{"PATH=/usr/bin:/bin", "JOBSEEK_NATIVE_CLIENT_MODE=" + mode, "JOBSEEK_NATIVE_CLIENT_REQUEST=" + base64.StdEncoding.EncodeToString(payload)}
	if granted != 0 {
		environment = append(environment, "JOBSEEK_NATIVE_CLIENT_AUTHORIZED_LEASE="+strconv.FormatInt(granted, 10))
	}
	return installedCommand(self, environment, uid, "-test.run=^TestInstalledNativeClientHelper$")
}

func installedResponse(t *testing.T, request Request, mode string, uid uint32) map[string]json.RawMessage {
	t.Helper()
	output, err := installedClient(t, request, mode, uid).CombinedOutput()
	if err != nil {
		t.Fatalf("installed client failed: %v %s", err, output)
	}
	var response map[string]json.RawMessage
	if json.Unmarshal(output, &response) != nil {
		t.Fatalf("invalid client response: %s", output)
	}
	return response
}

// The description lock holds the native transaction after its posting UPDATE.
// A queued exclusive posting lock then wins immediately after COMMIT and blocks
// ReadSchedule. This establishes a durable commit before any acknowledgement
// can be generated, without adding fault hooks to the production executable.
func installedCrashBeforeAcknowledgement(t *testing.T, owner *Executor, request Request, stop func(syscall.Signal) error, granted int64) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	locker, err := pgx.Connect(ctx, os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal("isolated lock fixture connection unavailable")
	}
	defer locker.Close(context.Background())
	blocker, err := owner.Store.pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	if _, err := blocker.Exec(ctx, "LOCK TABLE descriptions IN ACCESS EXCLUSIVE MODE"); err != nil {
		t.Fatal(err)
	}
	client := installedClientAuthorized(t, request, "commit", installedUID, granted)
	var output installedLog
	client.Stdout, client.Stderr = &output, &output
	if err := client.Start(); err != nil {
		t.Fatal(err)
	}
	clientDone := make(chan error, 1)
	go func() { clientDone <- client.Wait() }()
	defer func() { _ = client.Process.Kill() }()
	waitFor := func(stage, query string, args ...any) {
		t.Helper()
		deadline := time.Now().Add(3 * time.Second)
		for {
			if _, err := blocker.Exec(ctx, "SELECT pg_stat_clear_snapshot()"); err != nil {
				t.Fatal(err)
			}
			var ready bool
			if err := blocker.QueryRow(ctx, query, args...).Scan(&ready); err != nil {
				t.Fatal(err)
			}
			if ready {
				return
			}
			if time.Now().After(deadline) {
				t.Fatalf("installed %s barrier was not reached", stage)
			}
			time.Sleep(10 * time.Millisecond)
		}
	}
	waitFor("description", "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:lightpanda-b0-executor:local' AND wait_event_type='Lock' AND query LIKE '%WITH prior AS MATERIALIZED%')")
	locked := make(chan error, 1)
	lockCompleted := false
	go func() {
		_, err := locker.Exec(ctx, "BEGIN; LOCK TABLE job_posting IN ACCESS EXCLUSIVE MODE")
		locked <- err
	}()
	// Cancellation/rollback releases the synthetic barriers even if a check
	// fails, so fixture cleanup never waits for a blocked production handler.
	defer func() {
		cancel()
		_ = stop(syscall.SIGKILL)
		if !lockCompleted {
			select {
			case <-locked:
			case <-time.After(3 * time.Second):
				t.Error("isolated lock fixture did not cancel")
			}
		}
	}()
	waitFor("queued posting lock", "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=$1 AND relation='job_posting'::regclass AND mode='AccessExclusiveLock' AND NOT granted)", int32(locker.PgConn().PID()))
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-locked:
		lockCompleted = true
		if err != nil {
			t.Fatal("post-commit acknowledgement barrier failed")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("native transaction did not commit within fixture barrier")
	}
	defer func() { _, _ = locker.Exec(context.Background(), "ROLLBACK") }()
	var title []string
	var scraped *time.Time
	if err := locker.QueryRow(ctx, "SELECT titles,last_scraped_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&title, &scraped); err != nil || len(title) != 1 || title[0] != "Senior Software Engineer" || scraped == nil {
		t.Fatal("hard-kill fixture did not establish durable native commit")
	}
	// The native connection must now be blocked in its post-commit schedule
	// read. A generated acknowledgement cannot precede that read completing.
	deadline := time.Now().Add(3 * time.Second)
	for {
		if _, err := locker.Exec(ctx, "SELECT pg_stat_clear_snapshot()"); err != nil {
			t.Fatal(err)
		}
		var waiting bool
		if err := locker.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:lightpanda-b0-executor:local' AND wait_event_type='Lock' AND query LIKE 'SELECT is_active, next_scrape_at FROM job_posting%')").Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("native owner was not held before acknowledgement generation")
		}
		time.Sleep(10 * time.Millisecond)
	}
	_ = stop(syscall.SIGKILL)
	select {
	case err := <-clientDone:
		if err == nil || bytes.Contains([]byte(output.String()), []byte(`"type":"committed"`)) {
			t.Fatal("hard-killed owner delivered a commit acknowledgement")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("hard-killed owner retained client conversation")
	}
}

func TestInstalledNativeExecutorStartupBudgetHealthAndRecovery(t *testing.T) {
	binary := os.Getenv("JOBSEEK_B0_EXECUTOR_INTEGRATION_BINARY")
	if binary == "" {
		t.Skip("installed native integration binary not configured")
	}
	if os.Geteuid() != 0 {
		t.Fatal("installed fixture requires root to drop to UID 10001")
	}
	owner, request := executorFixture(t)
	ctx := context.Background()
	// Distinguish the driver from the actual native owner's process sessions.
	if _, err := owner.Store.pool.Exec(ctx, "SET application_name='jobseek:native-executor-fixture-driver'"); err != nil {
		t.Fatal(err)
	}
	schema := "installed_" + strings.ReplaceAll(fixtureID(t), "-", "")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err := owner.Store.pool.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = owner.Store.pool.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE") })
	if _, err := owner.Store.pool.Exec(ctx, `CREATE TABLE `+quoted+`.technology(id bigint,slug text);
CREATE TABLE `+quoted+`.occupation(id bigint,slug text);
CREATE TABLE `+quoted+`.seniority(id bigint,slug text);
CREATE TABLE `+quoted+`.currency_rate(currency text,to_eur numeric);
CREATE TABLE `+quoted+`.location(id bigint,parent_id bigint,type text,population bigint,languages text[]);
CREATE TABLE `+quoted+`.location_name(location_id bigint,locale text,name text,is_display boolean);
INSERT INTO `+quoted+`.technology VALUES(4,'python');
INSERT INTO `+quoted+`.occupation VALUES(41,'software-engineer');
INSERT INTO `+quoted+`.seniority VALUES(7,'senior'),(9,'intern');
INSERT INTO `+quoted+`.currency_rate VALUES('CHF',1.05);
INSERT INTO `+quoted+`.location VALUES(1,NULL,'country',NULL,ARRAY['de']),(2,1,'city',400000,ARRAY['de']);
INSERT INTO `+quoted+`.location_name VALUES(1,'en','Switzerland',true),(2,'en','Zurich',true),(2,'de','Zürich',true);`); err != nil {
		t.Fatal(err)
	}
	request.Result = renderedFixture(`<script type="application/ld+json">{"@type":"JobPosting","title":"Senior Software Engineer","description":"<p>Python engineering. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","jobLocation":{"@type":"Place","address":{"addressLocality":"Zurich","addressCountry":"Switzerland"}}}</script>`, request.Task.Envelope.SourceURL, 200)
	dsn, err := url.Parse(os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	query := dsn.Query()
	query.Set("search_path", schema+",public")
	dsn.RawQuery = query.Encode()
	temp, err := os.MkdirTemp("/tmp", "jobseek-installed-index-")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(temp, int(installedUID), int(installedUID)); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(temp) })
	environment := []string{"PATH=/usr/bin:/bin", "GOMAXPROCS=2", "TMPDIR=" + temp, "LOCAL_DATABASE_URL=" + dsn.String(), "LIGHTPANDA_B0_EXECUTOR_MODE=enabled", "CRAWLER_DB_POOL_MIN=1", "CRAWLER_DB_POOL_MAX=1", "LIGHTPANDA_B0_SHARD_ID=lightpanda-b0", "LIGHTPANDA_B0_ROUTING_EPOCH=" + strconv.FormatInt(owner.Epoch, 10)}
	var process *exec.Cmd
	var done chan error
	var log installedLog
	stop := func(signal syscall.Signal) error {
		if process == nil {
			return nil
		}
		_ = process.Process.Signal(signal)
		select {
		case err := <-done:
			process = nil
			return err
		case <-time.After(18 * time.Second):
			_ = process.Process.Kill()
			<-done
			process = nil
			return errors.New("native owner exceeded shutdown bound")
		}
	}
	t.Cleanup(func() { _ = stop(syscall.SIGKILL) })
	start := func() {
		t.Helper()
		process = installedCommand(binary, environment, installedUID)
		process.Stdout, process.Stderr = &log, &log
		if err := process.Start(); err != nil {
			t.Fatal(err)
		}
		done = make(chan error, 1)
		go func(command *exec.Cmd, completion chan error) { completion <- command.Wait() }(process, done)
		deadline := time.Now().Add(10 * time.Second)
		for {
			if err := installedCommand(binary, environment, installedUID, "--health").Run(); err == nil {
				break
			}
			select {
			case err := <-done:
				process = nil
				t.Fatalf("installed startup failed: %v %s", err, log.String())
			default:
			}
			if time.Now().After(deadline) {
				t.Fatalf("installed readiness expired: %s", log.String())
			}
			time.Sleep(25 * time.Millisecond)
		}
	}
	start()
	info, err := os.Stat(SocketPath)
	if err != nil || info.Mode().Perm() != 0o600 || info.Sys().(*syscall.Stat_t).Uid != installedUID {
		t.Fatal("installed socket metadata differs")
	}
	var sessions int
	if err := owner.Store.pool.QueryRow(ctx, "SELECT count(*) FROM pg_stat_activity WHERE application_name='jobseek:crawler:lightpanda-b0-executor:local'").Scan(&sessions); err != nil || sessions != 1 {
		t.Fatalf("installed owner connection budget: %d %v", sessions, err)
	}
	if response := installedResponse(t, request, "wrong_peer", 0); string(response["type"]) != `"rejected"` {
		t.Fatal("root peer bypassed same-UID check")
	}
	// Hold all four tasks before authorization. Health stays available and the
	// fifth task cannot occupy the health conversation or create a write fence.
	var clients []*exec.Cmd
	var admissions []io.Reader
	for range TaskCapacity {
		client := installedClient(t, request, "hold", installedUID)
		output, err := client.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		input, err := client.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		if err := client.Start(); err != nil {
			t.Fatal(err)
		}
		clients = append(clients, client)
		admissions = append(admissions, output)
		t.Cleanup(func() { _ = input.Close(); _ = client.Process.Kill() })
	}
	for _, output := range admissions {
		line, err := bufio.NewReader(output).ReadBytes('\n')
		if err != nil || !bytes.Contains(line, []byte(`"authorize"`)) {
			t.Fatal("installed task not admitted")
		}
	}
	if response := installedResponse(t, request, "capacity", installedUID); string(response["type"]) != `"rejected"` {
		t.Fatal("fifth task admitted")
	}
	if err := installedCommand(binary, environment, installedUID, "--health").Run(); err != nil {
		t.Fatal("installed health lost reserved slot")
	}
	for _, client := range clients {
		_ = client.Process.Kill()
		_ = client.Wait()
	}
	time.Sleep(30 * time.Millisecond)
	queue := newInstalledRedisQueue(t, request)
	request = queue.claim(t, 30000)
	granted := queue.authorize(t, request)
	installedCrashBeforeAcknowledgement(t, owner, request, stop, granted)
	queue.census(t, 0, 1, 0)
	var titles []string
	var ids, technologies []int64
	var occupation, seniority, salary int64
	var next time.Time
	if err := owner.Store.pool.QueryRow(ctx, "SELECT titles,location_ids,technology_ids,occupation_id,seniority_id,salary_min,next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&titles, &ids, &technologies, &occupation, &seniority, &salary, &next); err != nil {
		t.Fatal(err)
	}
	if len(titles) != 1 || titles[0] != "Senior Software Engineer" || len(ids) != 1 || ids[0] != 2 || len(technologies) != 1 || technologies[0] != 4 || occupation != 41 || seniority != 7 || salary != 100000 {
		t.Fatal("installed native taxonomy/persistence differs")
	}
	// Model the description consumer finishing between the first durable commit
	// and retry. Equal held HTML must retain its completed upload and timestamp.
	if _, err := owner.Store.pool.Exec(ctx, "UPDATE descriptions SET r2_uploaded=true WHERE posting_id=$1", request.Task.Envelope.TaskID); err != nil {
		t.Fatal(err)
	}
	canonicalSnapshot := func() []byte {
		t.Helper()
		var snapshot []byte
		const query = `SELECT jsonb_build_object('posting',to_jsonb(jp)-ARRAY['updated_at','last_scraped_at','next_scrape_at'],
'description',to_jsonb(d)) FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id WHERE jp.id=$1`
		if err := owner.Store.pool.QueryRow(ctx, query, request.Task.Envelope.TaskID).Scan(&snapshot); err != nil {
			t.Fatal(err)
		}
		return snapshot
	}
	beforeRetry := canonicalSnapshot()
	_ = stop(syscall.SIGKILL)
	// Container restart remounts the owner's private tmpfs. Model that exact
	// ephemeral-state boundary here after the old process is confirmed dead.
	if err := os.RemoveAll(temp); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(temp, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(temp, int(installedUID), int(installedUID)); err != nil {
		t.Fatal(err)
	}
	start()
	response := installedResponse(t, request, "commit", installedUID)
	if string(response["type"]) != `"authority_lost"` {
		t.Fatal("restart allowed committed duplicate")
	}
	var after time.Time
	if err := owner.Store.pool.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&after); err != nil || !after.Equal(next) {
		t.Fatal("restart/duplicate changed committed schedule")
	}
	// The real queue still owns an inflight lease when the acknowledgement is
	// lost. Model the existing supervisor's EOF settlement through its exact
	// Lua fail_at ABI, then retry the same held HTML without another origin.
	settlement := request
	settlement.LeaseUntilMS = granted
	settlementGrant := queue.authorize(t, settlement)
	// Match heartbeat-before-fail and Redis-clock backoff in failAndFinish;
	// shorten only the isolated fixture's backoff to keep this test bounded.
	queue.accept(t, "fail_at", request, 0, settlementGrant-30000+100, settlementGrant, "")
	queue.census(t, 1, 0, 1)
	time.Sleep(150 * time.Millisecond)
	recovered := queue.claim(t, 30000)
	if recovered.ClaimToken == request.ClaimToken {
		t.Fatal("Redis recovery reused durable claim token")
	}
	rejected := queue.call(t, "complete", request, 0, 0, granted, "")
	if rejected[0] == "accepted" {
		t.Fatal("lost acknowledgement settled the recovered claim")
	}
	grant := queue.authorize(t, recovered)
	output, err := installedClientAuthorized(t, recovered, "commit", installedUID, grant).CombinedOutput()
	if err != nil {
		t.Fatal("installed recovered held-result conversation failed")
	}
	var ack map[string]json.RawMessage
	var acknowledgedReady, acknowledgedLease int64
	if json.Unmarshal(output, &ack) != nil || string(ack["type"]) != `"committed"` || json.Unmarshal(ack["next_ready_at_ms"], &acknowledgedReady) != nil || json.Unmarshal(ack["lease_until_ms"], &acknowledgedLease) != nil || acknowledgedLease != grant {
		t.Fatal("recovered native acknowledgement differs")
	}
	if err := owner.Store.pool.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1", recovered.Task.Envelope.TaskID).Scan(&next); err != nil || acknowledgedReady != next.UnixMilli() {
		t.Fatal("recovered acknowledgement differs from PostgreSQL schedule")
	}
	if !bytes.Equal(beforeRetry, canonicalSnapshot()) {
		t.Fatal("held-result retry changed canonical content or completed description upload")
	}
	queue.accept(t, "reschedule_at", recovered, 0, acknowledgedReady, grant, "")
	queue.census(t, 1, 0, 0)
	score, err := queue.client.ZScore(ctx, queue.keys[2], recovered.Task.Envelope.TaskID).Result()
	if err != nil || score != float64(acknowledgedReady) {
		t.Fatal("recovered Redis schedule differs from native database")
	}
	if reply := installedResponse(t, recovered, "commit", installedUID); string(reply["type"]) != `"authority_lost"` {
		t.Fatal("recovered duplicate retained database authority")
	}
	if !bytes.Equal(beforeRetry, canonicalSnapshot()) {
		t.Fatal("recovered duplicate changed canonical content or description")
	}
	// Keep the stale-route assertions bound to the latest durable schedule.
	request = recovered
	// Current routing epoch is database-owned. A changed epoch must fail health
	// and reject an old-route task before any new authoritative content effects.
	if _, err := owner.Store.pool.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	if err := installedCommand(binary, environment, installedUID, "--health").Run(); err == nil {
		t.Fatal("stale installed route reported healthy")
	}
	response = installedResponse(t, request, "commit", installedUID)
	if string(response["type"]) != `"authority_lost"` {
		t.Fatal("stale installed route retained task authority")
	}
	if err := owner.Store.pool.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1", request.Task.Envelope.TaskID).Scan(&after); err != nil || !after.Equal(next) {
		t.Fatal("stale route changed committed schedule")
	}
	if err := stop(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(SocketPath); !os.IsNotExist(err) {
		t.Fatal("normal shutdown retained owned socket")
	}
	entries, err := os.ReadDir(temp)
	if err != nil || len(entries) != 0 {
		t.Fatal("normal shutdown retained private index")
	}
}
