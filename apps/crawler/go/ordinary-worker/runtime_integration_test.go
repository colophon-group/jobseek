package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestRealNativeExecutableStartupOwnershipAssetsMetricsAndSignalDrain(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	dsn := privatePipelineReferenceDSN(t, f)
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal("private reservation failed")
	}
	e := newNativeExecutableFixture(t, f, dsn)
	binary, env, address := e.binary, e.env, e.address
	p := e.start(t, "startup")
	command, done := p.command, p.done
	httpClient := &http.Client{Timeout: time.Second}
	defer httpClient.CloseIdleConnections()
	deadline := time.Now().Add(20 * time.Second)
	expectedMetrics := []string{`crawler_tasks_total{kind="monitor",status="tdm_reserved"} 1`, `crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 0`, `jobseek_ordinary_go_active_claims 0`}
	settled := false
	for time.Now().Before(deadline) {
		select {
		case <-done:
			p.finished = true
			t.Fatal("native executable exited before owned cycle")
		default:
		}
		var state string
		_ = f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state)
		response, err := httpClient.Get("http://" + address + "/healthz")
		if err == nil {
			healthy := response.StatusCode == http.StatusNoContent
			_ = response.Body.Close()
			if healthy && state == "completed" && f.r.ZCard(ctx, "inflight:simple").Val() == 0 {
				// DB completion and Redis ACK precede outcome accounting and
				// active-claim release. Observe the whole settled contract.
				metrics, err := httpClient.Get("http://" + address + "/metrics")
				if err == nil {
					body := readFixtureBody(t, metrics)
					settled = true
					for _, expected := range expectedMetrics {
						settled = settled && strings.Contains(body, expected)
					}
					if settled {
						break
					}
				}
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !settled {
		t.Fatal("installed native process did not complete no-fetch publisher cycle")
	}
	healthCommand := exec.Command(binary, "--health")
	healthCommand.Env = env
	if err := healthCommand.Run(); err != nil {
		t.Fatal("native process identity-bound health probe failed")
	}
	response, err := httpClient.Get("http://" + address + "/metrics")
	if err != nil {
		t.Fatal(err)
	}
	body := readFixtureBody(t, response)
	for _, expected := range expectedMetrics {
		if !strings.Contains(body, expected) {
			t.Fatal("native process did not expose policy/queue/network conservation", expected)
		}
	}
	if err := command.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	if err := p.wait(t); err != nil {
		t.Fatal("native executable did not drain on signal", err)
	}
	var failures int
	var due time.Time
	if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &due); err != nil || failures != 0 {
		t.Fatal("publisher reservation spent failure budget")
	}
	if score := f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Val(); score != float64(due.UnixMicro())/1e6 {
		t.Fatal("native process lost canonical reservation due")
	}
	// Incorrect installed projection refuses startup before claiming future work.
	wrong := exec.Command(binary)
	wrong.Env = append(env, "ORDINARY_OWNERSHIP_PROJECTION_SHA1="+strings.Repeat("d", 40))
	if output, err := wrong.CombinedOutput(); err == nil || !strings.Contains(string(output), "ordinary worker command rejected") || !strings.Contains(string(output), "ordinary worker startup stage: projection") || strings.Contains(string(output), dsn) {
		t.Fatal("wrong installed projection accepted startup")
	}
	// Asset failures retain a static stage while keeping private paths out of logs.
	missing := exec.Command(binary)
	missing.Env = append(env, "ORDINARY_GO_DATA_DIRECTORY="+filepath.Join(t.TempDir(), "private-do-not-log"))
	if output, err := missing.CombinedOutput(); err == nil || !strings.Contains(string(output), "ordinary worker startup stage: enrichment_assets") || strings.Contains(string(output), "private-do-not-log") || strings.Contains(string(output), dsn) {
		t.Fatal("missing assets lost rejection stage or leaked private input")
	}
	invalid := exec.Command(binary)
	invalid.Env = append(env, "ORDINARY_OWNERSHIP_PLAN_SHA256=private-do-not-log")
	if output, err := invalid.CombinedOutput(); err == nil || !strings.Contains(string(output), "ordinary worker startup stage: configuration") || strings.Contains(string(output), "private-do-not-log") || strings.Contains(string(output), dsn) {
		t.Fatal("invalid configuration lost rejection stage or leaked private input")
	}
}

// Installed-image admission supplies all three bindings. Without them the
// local source fixture deliberately uses a synthetic revision, never an image
// admission claim. These switches exist only in test code.
func ordinaryFixtureSourceRevision(t *testing.T) string {
	t.Helper()
	binary := os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY")
	revision := os.Getenv("JOBSEEK_ORDINARY_IMAGE_SOURCE_REVISION")
	data := os.Getenv("JOBSEEK_ORDINARY_IMAGE_DATA_DIRECTORY")
	if binary == "" && revision == "" && data == "" {
		return strings.Repeat("a", 40)
	}
	if !filepath.IsAbs(binary) || !filepath.IsAbs(data) || !sourcePattern.MatchString(revision) {
		t.Fatal("installed fixture requires exact source, absolute binary and assets")
	}
	return revision
}

type nativeExecutableFixture struct {
	binary, address, directory string
	env                        []string
}

func newNativeExecutableFixture(t *testing.T, f nativePipelineFixture, dsn string) nativeExecutableFixture {
	t.Helper()
	ctx := context.Background()
	var plan, projectionBody, revision string
	var epoch int64
	if err := f.pg.QueryRow(ctx, "SELECT plan_sha256,payload,source_revision,routing_epoch FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&plan, &projectionBody, &revision, &epoch); err != nil {
		t.Fatal("private active plan missing")
	}
	projection, err := queue.OwnershipProjectionSHA1(projectionBody, plan)
	if err != nil {
		t.Fatal("private routing projection invalid")
	}
	directory := t.TempDir()
	binary := os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY")
	dataDirectory := os.Getenv("JOBSEEK_ORDINARY_IMAGE_DATA_DIRECTORY")
	if binary == "" {
		binary = filepath.Join(directory, "ordinary-worker")
		build := exec.Command("go", "build", "-buildvcs=false", "-ldflags=-X main.sourceRevision="+revision, "-o", binary, "./cmd/live")
		if output, err := build.CombinedOutput(); err != nil {
			t.Fatalf("native executable fixture build failed: %v\n%s", err, output)
		}
		var err error
		dataDirectory, err = filepath.Abs("../../data")
		if err != nil {
			t.Fatal(err)
		}
	}
	identityOutput, err := exec.Command(binary, "--identity").Output()
	var identity BuildIdentity
	if err != nil || json.Unmarshal(identityOutput, &identity) != nil || identity != (BuildIdentity{revision, pinnedCASHA256, "greenhouse.token-skip/v1", [30]string{"greenhouse.token-skip/v1", "ashby.token-skip/v1", "lever.token-skip/v1", "recruitee.api-skip/v1", "pinpoint.slug-skip/v1", "rss.teamtailor-skip/v1", "rss.successfactors-skip/v1", "personio.xml-skip/v1", "workday.cxs-urls/v1", "workday.cxs-detail/v1", "jsonld.direct-detail/v1", "smartrecruiters.api-detail/v1", "workable.api-detail/v1", "smartrecruiters.api-urls/v1", "workable.api-urls/v1", "join.nextdata-urls/v1", "join.nextdata-detail/v1", "sitemap.explicit-urls/v1", "dom.direct-detail/v1", "dom.direct-urls/v1", "dom.rendered-urls/v1", "dom.rendered-detail/v1", "jsonld.rendered-detail/v1", "api_sniffer.http-items/v1", "oracle_hcm.finder-items/v1", "oracle_hcm.api-detail/v1", "embedded.direct-detail/v1", "embedded.rendered-detail/v1", "api_sniffer.http-detail/v1", "icims.listing-urls/v1"}}) {
		t.Fatal("native executable lost compiled source/CA/profile identities")
	}
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	_ = listener.Close()
	env := []string{"PATH=" + os.Getenv("PATH"), "ORDINARY_GO_WORKER_MODE=enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + revision, "ORDINARY_OWNERSHIP_PLAN_SHA256=" + plan, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + projection, "ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + fmt.Sprint(epoch), "LOCAL_DATABASE_URL=" + dsn, "REDIS_URL=unix://" + f.r.Options().Addr, "ORDINARY_GO_METRICS_ADDRESS=" + address, "ORDINARY_GO_DATA_DIRECTORY=" + dataDirectory, "DISCOVERY_CONCURRENCY=1", "MONITOR_CONCURRENCY=1", "SHUTDOWN_GRACE_SECONDS=1"}
	return nativeExecutableFixture{binary: binary, address: address, directory: directory, env: env}
}

type nativeFixtureProcess struct {
	command  *exec.Cmd
	done     chan error
	finished bool
}

func (p *nativeFixtureProcess) wait(t *testing.T) error {
	t.Helper()
	err := awaitRuntime(t, p.done)
	p.finished = true
	return err
}

func (e nativeExecutableFixture) start(t *testing.T, name string) *nativeFixtureProcess {
	t.Helper()
	command := exec.Command(e.binary)
	command.Env = e.env
	log, err := os.OpenFile(filepath.Join(e.directory, name+".log"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = log.Close() })
	command.Stdout, command.Stderr = log, log
	if err := command.Start(); err != nil {
		t.Fatal("native executable startup failed")
	}
	p := &nativeFixtureProcess{command: command, done: make(chan error, 1)}
	go func() { p.done <- command.Wait() }()
	t.Cleanup(func() {
		if !p.finished {
			_ = command.Process.Kill()
			<-p.done
		}
	})
	return p
}

func waitNativeFixture(t *testing.T, p *nativeFixtureProcess, what string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(20 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-p.done:
			p.finished = true
			t.Fatal("native process exited before " + what)
		default:
		}
		if condition() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("native process did not reach " + what)
}

func TestRealNativeExecutableSIGKILLAfterCommitBeforeAckRecoversWithoutReplay(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	dsn := privatePipelineReferenceDSN(t, f)
	if _, err := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); err != nil {
		t.Fatal("private reservation failed")
	}

	// Pause only this private board's terminal transaction. No executable fault
	// hooks, production schema changes or relaxed network trust are involved.
	name := "ordinary_crash_" + strings.ReplaceAll(f.board, "-", "")
	quoted := pgx.Identifier{name}.Sanitize()
	const lockClass, lockObject = 18273645, 914273
	if _, err := f.pg.Exec(ctx, "CREATE FUNCTION "+quoted+"() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_advisory_xact_lock(18273645,914273); RETURN NEW; END $$"); err != nil {
		t.Fatal("private commit pause function failed")
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "DROP TRIGGER IF EXISTS "+quoted+" ON ordinary_worker_write_fence")
		_, _ = f.pg.Exec(context.Background(), "DROP FUNCTION "+quoted+"()")
	})
	if _, err := f.pg.Exec(ctx, "CREATE TRIGGER "+quoted+" AFTER UPDATE ON ordinary_worker_write_fence FOR EACH ROW WHEN (OLD.state='active' AND NEW.state='completed' AND NEW.task_id='"+f.board+"'::uuid) EXECUTE FUNCTION "+quoted+"()"); err != nil {
		t.Fatal("private terminal pause trigger failed")
	}
	lock, err := f.pg.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Release()
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_lock($1,$2)", lockClass, lockObject); err != nil {
		t.Fatal(err)
	}
	defer func() {
		_, _ = lock.Exec(context.Background(), "SELECT pg_advisory_unlock($1,$2)", lockClass, lockObject)
	}()
	e := newNativeExecutableFixture(t, f, dsn)
	p := e.start(t, "before-crash")
	waitNativeFixture(t, p, "private terminal commit barrier", func() bool {
		var waiting bool
		_ = f.pg.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=$1 AND objid=$2 AND NOT granted)", lockClass, lockObject).Scan(&waiting)
		return waiting
	})
	member := "monitor|greenhouse|" + f.board
	token := f.r.HGet(ctx, "inflight_tokens:simple", member).Val()
	if len(token) != 32 || f.r.ZCard(ctx, "inflight:simple").Val() != 1 {
		t.Fatal("terminal pause lacks exactly one tokenized lease")
	}
	snapshot := f.r.HGetAll(ctx, "board:"+f.board).Val()
	if len(snapshot) == 0 {
		t.Fatal("private claim snapshot missing")
	}
	var boardBefore, postingBefore string
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_posting j WHERE id=$1::uuid", f.original).Scan(&postingBefore); err != nil {
		t.Fatal(err)
	}
	// Corrupt only the disposable index after claim. A guarded acknowledgement
	// must refuse before deleting the lease or changing any queue/config state.
	if err := f.r.Set(ctx, "ready:simple:1", "private acknowledgement fault", 0).Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := lock.Exec(ctx, "SELECT pg_advisory_unlock($1,$2)", lockClass, lockObject); err != nil {
		t.Fatal(err)
	}
	client := &http.Client{Timeout: time.Second}
	defer client.CloseIdleConnections()
	metrics := func() string {
		response, err := client.Get("http://" + e.address + "/metrics")
		if err != nil {
			return ""
		}
		return readFixtureBody(t, response)
	}
	waitNativeFixture(t, p, "committed unacknowledged receipt", func() bool {
		return strings.Contains(metrics(), `crawler_tasks_total{kind="monitor",status="unacknowledged"} 1`)
	})
	var state, receiptBefore string
	var due time.Time
	var failures int
	if err := f.pg.QueryRow(ctx, "SELECT state,(to_jsonb(f)-'claim_token'-'updated_at')::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&state, &receiptBefore); err != nil || state != "completed" {
		t.Fatal("unacknowledged process lacks committed terminal receipt")
	}
	if err := f.pg.QueryRow(ctx, "SELECT next_check_at,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&due, &failures); err != nil || failures != 0 || !due.After(time.Now()) {
		t.Fatal("unacknowledged receipt lost canonical future due/budget")
	}
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_board j WHERE id=$1::uuid", f.board).Scan(&boardBefore); err != nil {
		t.Fatal(err)
	}
	if f.r.HGet(ctx, "inflight_tokens:simple", member).Val() != token || f.r.ZCard(ctx, "inflight:simple").Val() != 1 {
		t.Fatal("failed acknowledgement revoked native lease")
	}
	if current := f.r.HGetAll(ctx, "board:"+f.board).Val(); !reflect.DeepEqual(current, snapshot) {
		t.Fatal("failed acknowledgement changed claim snapshot")
	}
	if err := p.command.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	crash := p.wait(t)
	var exit *exec.ExitError
	if !errors.As(crash, &exit) {
		t.Fatal("SIGKILL lacks abnormal process exit")
	}
	if status, ok := exit.Sys().(syscall.WaitStatus); !ok || !status.Signaled() || status.Signal() != syscall.SIGKILL {
		t.Fatal("fixture process did not terminate with SIGKILL")
	}
	// A later unrelated circuit failure must survive receipt recovery.
	if err := f.r.Set(ctx, "host_fail:job-boards.greenhouse.io", "2", time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.r.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: member}).Err(); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("../../src/lua/reap_expired.lua")
	if err != nil {
		t.Fatal(err)
	}
	if err := queue.WithOrdinaryLeaseRetirement(ctx, f.pg, func(ctx context.Context) error {
		now, err := f.r.Time(ctx).Result()
		if err != nil {
			return err
		}
		at := float64(now.UnixMicro()) / 1e6
		return f.r.Eval(ctx, string(body), nil, "simple", at, 10, 3, at, "guarded").Err()
	}); err != nil {
		t.Fatal("guarded private lease retirement failed", err)
	}
	if f.r.HExists(ctx, "inflight_tokens:simple", member).Val() || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("reaper retained killed generation")
	}
	p = e.start(t, "after-crash")
	waitNativeFixture(t, p, "recovered native receipt", func() bool {
		return strings.Contains(metrics(), `crawler_tasks_total{kind="monitor",status="recovered"} 1`) && f.r.ZCard(ctx, "inflight:simple").Val() == 0
	})
	for _, expected := range []string{`crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 0`, `jobseek_ordinary_go_active_claims 0`} {
		if !strings.Contains(metrics(), expected) {
			t.Fatal("restarted process replayed fetch or retained claim")
		}
	}
	var receiptAfter, boardAfter, postingAfter, recoveredToken string
	if err := f.pg.QueryRow(ctx, "SELECT (to_jsonb(f)-'claim_token'-'updated_at')::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&receiptAfter); err != nil || receiptBefore != receiptAfter {
		t.Fatal("recovery repeated terminal database effects")
	}
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_board j WHERE id=$1::uuid", f.board).Scan(&boardAfter); err != nil || boardAfter != boardBefore {
		t.Fatal("recovery repeated canonical board effects")
	}
	if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(j)::text FROM job_posting j WHERE id=$1::uuid", f.original).Scan(&postingAfter); err != nil || postingAfter != postingBefore {
		t.Fatal("recovery changed retained posting")
	}
	if err := f.pg.QueryRow(ctx, "SELECT claim_token FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&recoveredToken); err != nil || recoveredToken == token {
		t.Fatal("restart reused killed claim generation")
	}
	for _, outcome := range []string{"success", "error", "cancelled"} {
		if !strings.Contains(metrics(), fmt.Sprintf(`crawler_runtime_executions_total{stage="monitor",implementation="go",outcome=%q} 0`, outcome)) {
			t.Fatal("receipt recovery repeated native extraction")
		}
	}
	health := exec.Command(e.binary, "--health")
	health.Env = e.env
	if err := health.Run(); err != nil {
		t.Fatal("restarted native health identity failed")
	}
	var dueAfter time.Time
	var failuresAfter int
	if err := f.pg.QueryRow(ctx, "SELECT next_check_at,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&dueAfter, &failuresAfter); err != nil || !due.Equal(dueAfter) || failuresAfter != failures || f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Val() != float64(due.UnixMicro())/1e6 {
		t.Fatal("recovery changed canonical deadline/failure budget or Redis schedule")
	}
	if f.r.Get(ctx, "host_fail:job-boards.greenhouse.io").Val() != "2" || f.r.HExists(ctx, "inflight_tokens:simple", member).Val() {
		t.Fatal("recovery replayed circuit outcome or retained token")
	}
	if err := p.command.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	if err := p.wait(t); err != nil {
		t.Fatal("restarted executable failed signal drain", err)
	}
}
func readFixtureBody(t *testing.T, response *http.Response) string {
	t.Helper()
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestRealLeasedRunnerLosesTokenCancelsVerifiedFetchWithoutWrites(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	entered := make(chan struct{}, 1)
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		entered <- struct{}{}
		select {
		case <-r.Context().Done():
		case <-time.After(3 * time.Second):
			w.WriteHeader(503)
		}
	}))
	c := fixtureLoopConfig()
	c.heartbeat = 10 * time.Millisecond
	c.taskTimeout = 2 * time.Second
	m := newRuntimeMetrics(1, c.stallTimeout)
	services := runtimeServices{heartbeat: f.a.Heartbeat, execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
		return RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
	}}
	done := make(chan struct {
		result *GreenhouseClaimResult
		err    error
	}, 1)
	go func() {
		result, err := runLeasedClaim(ctx, c, services, claim, m)
		done <- struct {
			result *GreenhouseClaimResult
			err    error
		}{result, err}
	}()
	awaitRuntime(t, entered)
	if err := f.r.HSet(ctx, "inflight_tokens:simple", "monitor|greenhouse|"+f.board, strings.Repeat("f", 32)).Err(); err != nil {
		t.Fatal(err)
	}
	outcome := awaitRuntime(t, done)
	if !errors.Is(outcome.err, queue.ErrAuthorityLost) || outcome.result.Settled || outcome.result.Cycle != nil || m.lost != 1 {
		t.Fatal("lost token did not cancel native verified fetch without terminal authority", outcome.err)
	}
	if outcome.result.HTTP.Requests != 1 || outcome.result.HTTP.Responses+outcome.result.HTTP.NoResponse != 1 {
		t.Fatal("cancelled fetch lost origin conservation")
	}
	var failures int
	var state string
	if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil || failures != 0 {
		t.Fatal("claim loss spent canonical failure budget")
	}
	if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state); err != nil || state != "active" {
		t.Fatal("claim loss fabricated terminal receipt")
	}
}
