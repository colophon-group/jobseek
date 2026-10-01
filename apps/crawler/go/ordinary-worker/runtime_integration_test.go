package worker

import (
	"context"
	"crypto/sha1"
	"encoding/json"
	"errors"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
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
	var plan, projectionBody, revision string
	var epoch int64
	if err := f.pg.QueryRow(ctx, "SELECT plan_sha256,payload,source_revision,routing_epoch FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&plan, &projectionBody, &revision, &epoch); err != nil {
		t.Fatal("private active plan missing")
	}
	// This binary is built from current fixture source with an explicit fixture
	// revision. Production admission must bind the actual immutable image source.
	directory := t.TempDir()
	binary := filepath.Join(directory, "ordinary-worker")
	build := exec.Command("go", "build", "-buildvcs=false", "-ldflags=-X main.sourceRevision="+revision, "-o", binary, "./cmd/live")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("native executable fixture build failed: %v\n%s", err, output)
	}

	identityCommand := exec.Command(binary, "--identity")
	identityOutput, err := identityCommand.Output()
	if err != nil {
		t.Fatal("native executable identity unavailable")
	}
	var identity BuildIdentity
	if json.Unmarshal(identityOutput, &identity) != nil || identity != (BuildIdentity{revision, pinnedCASHA256, "greenhouse.token-skip/v1"}) {
		t.Fatal("native executable lost compiled source/CA/profile identities")
	}

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	address := listener.Addr().String()
	_ = listener.Close()
	dataDirectory, err := filepath.Abs("../../data")
	if err != nil {
		t.Fatal(err)
	}
	// Recover the fixture's private UDS from its observer; never print the URL.
	socket := f.r.Options().Addr
	env := []string{"PATH=" + os.Getenv("PATH"), "ORDINARY_GO_WORKER_MODE=enabled", "ORDINARY_OWNERSHIP_SOURCE_REVISION=" + revision, "ORDINARY_OWNERSHIP_PLAN_SHA256=" + plan, "ORDINARY_OWNERSHIP_PROJECTION_SHA1=" + fmt.Sprintf("%x", sha1Fixture(projectionBody)), "ORDINARY_OWNERSHIP_ROUTING_EPOCH=" + fmt.Sprint(epoch), "LOCAL_DATABASE_URL=" + dsn, "REDIS_URL=unix://" + socket, "ORDINARY_GO_METRICS_ADDRESS=" + address, "ORDINARY_GO_DATA_DIRECTORY=" + dataDirectory, "DISCOVERY_CONCURRENCY=1", "MONITOR_CONCURRENCY=1", "SHUTDOWN_GRACE_SECONDS=1"}
	command := exec.Command(binary)
	command.Env = env
	log, err := os.OpenFile(filepath.Join(directory, "worker.log"), os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer log.Close()
	command.Stdout, command.Stderr = log, log
	if err := command.Start(); err != nil {
		t.Fatal("native executable startup failed")
	}
	done := make(chan error, 1)
	go func() { done <- command.Wait() }()
	finished := false
	t.Cleanup(func() {
		if !finished {
			_ = command.Process.Kill()
			<-done
		}
	})
	httpClient := &http.Client{Timeout: time.Second}
	defer httpClient.CloseIdleConnections()
	deadline := time.Now().Add(20 * time.Second)
	settled := false
	for time.Now().Before(deadline) {
		select {
		case <-done:
			finished = true
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
				settled = true
				break
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
	for _, expected := range []string{`crawler_tasks_total{kind="monitor",status="tdm_reserved"} 1`, `crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 0`, `jobseek_ordinary_go_active_claims 0`} {
		if !strings.Contains(body, expected) {
			t.Fatal("native process did not expose policy/queue/network conservation", expected)
		}
	}
	if err := command.Process.Signal(os.Interrupt); err != nil {
		t.Fatal(err)
	}
	if err := awaitRuntime(t, done); err != nil {
		t.Fatal("native executable did not drain on signal", err)
	}
	finished = true
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
	if output, err := wrong.CombinedOutput(); err == nil || !strings.Contains(string(output), "ordinary worker failed") {
		t.Fatal("wrong installed projection accepted startup")
	}
}

func sha1Fixture(body string) [20]byte { return sha1.Sum([]byte(body)) }
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
