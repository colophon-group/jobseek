package worker

import (
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/redis/go-redis/v9"
)

// CI's trusted Docker runner executes this test; an agent never gets a socket.
// The actual installed image/native command and original host driver perform
// SQL/Redis ownership effects and real container stopping/restart/readiness.
// Legacy/B0 services expose fixture health endpoints: their production behavior
// is covered separately, and this is not production freshness/coverage proof.
func TestRealFirstOwnershipHostContainersCutoverAndRecovery(t *testing.T) {
	image := os.Getenv("JOBSEEK_ORDINARY_HOST_IMAGE_ID")
	if image == "" {
		t.Skip("requires trusted CI's exact installed image and private Docker runner")
	}
	if !strings.HasPrefix(image, "sha256:") || !planPattern.MatchString(strings.TrimPrefix(image, "sha256:")) || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("host fixture requires the already admitted installed image")
	}
	for _, mode := range []string{"interrupted-retirement", "readiness-failure"} {
		t.Run(mode, func(t *testing.T) {
			f, e, plan := firstExecutableOwnershipFixture(t)
			ctx := context.Background()
			dsn := privatePipelineReferenceDSN(t, f)
			dir := e.directory
			project := "ordinary-host-" + strings.ReplaceAll(f.board, "-", "")
			command := func(name string, args ...string) ([]byte, error) {
				t.Helper()
				ctx, cancel := context.WithTimeout(context.Background(), 300*time.Second)
				defer cancel()
				cmd := exec.CommandContext(ctx, name, args...)
				cmd.Dir = dir
				cmd.Env = append(os.Environ(), "COMPOSE_PROJECT_NAME="+project)
				return cmd.CombinedOutput()
			}
			must := func(name string, args ...string) []byte {
				t.Helper()
				out, err := command(name, args...)
				if err != nil {
					if len(out) > 12000 {
						out = out[len(out)-12000:]
					}
					t.Fatal("private host command failed", name, err, strings.ReplaceAll(string(out), dsn, "[private fixture database]"))
				}
				return out
			}
			if got := strings.TrimSpace(string(must("docker", "image", "inspect", "-f", `{{index .Config.Labels "org.opencontainers.image.revision"}}`, image))); got != plan.SourceRevision() {
				t.Fatal("host fixture image source differs from the installed native source")
			}
			write := func(name, body string) {
				t.Helper()
				if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			// Locally built CI images have an immutable content ID, not a pushed
			// registry manifest. Keep the public request schema and bind both its
			// suffix and every Docker observation to that exact local image ID.
			// The fixture changes only host paths/user and these two image checks;
			// production image schema tests continue to require real GHCR digests.
			ref := "ghcr.io/ci/jobseek-crawler@" + image
			browser := "ghcr.io/ci/jobseek-crawler-browser@" + image
			write(".env", fmt.Sprintf("JOBSEEK_DEPLOY_REVISION=%s\nCRAWLER_IMAGE_REF=%s\nBROWSER_IMAGE_REF=%s\nLIGHTPANDA_B0_SERVICE_HOST=10.0.0.5\nFIXTURE_IMAGE_ID=%s\n", plan.SourceRevision(), ref, browser, image))
			write("health.py", `import http.server, pathlib, sys
port = int(sys.argv[1])
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if port == 9096 and pathlib.Path('/fixture/fail-health').exists() else 204)
        self.end_headers()
    def log_message(self, *args): pass
http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()
`)
			ports := map[string]int{"worker-1": 9095, "worker-2": 9096, "worker-3": 9097, "browser-1": 9098, "exporter": 9093, "drain": 9094, "lightpanda-producer": 0, "lightpanda-executor": 0, "lightpanda-claimant": 9101}
			var compose strings.Builder
			compose.WriteString("services:\n")
			for service, port := range ports {
				fmt.Fprintf(&compose, "  %s:\n    image: ${FIXTURE_IMAGE_ID}\n    network_mode: host\n    restart: unless-stopped\n    volumes: [%q]\n    entrypoint: [/app/.venv/bin/python, /fixture/health.py, %q]\n    command: []\n", service, dir+":/fixture:ro", fmt.Sprint(port))
			}
			fmt.Fprintf(&compose, `  ordinary-go:
    profiles: [ordinary-go]
    image: ${FIXTURE_IMAGE_ID}
    network_mode: host
    restart: "no"
    entrypoint: []
    command: [/usr/local/bin/go-ordinary-worker]
    volumes: [%q]
    environment:
      LOCAL_DATABASE_URL: %q
      REDIS_URL: %q
      ORDINARY_GO_WORKER_MODE: enabled
      ORDINARY_OWNERSHIP_SOURCE_REVISION: ${ORDINARY_OWNERSHIP_SOURCE_REVISION:-}
      ORDINARY_OWNERSHIP_ROUTING_EPOCH: ${ORDINARY_OWNERSHIP_ROUTING_EPOCH:-}
      ORDINARY_OWNERSHIP_PLAN_SHA256: ${ORDINARY_OWNERSHIP_PLAN_SHA256:-}
      ORDINARY_OWNERSHIP_PROJECTION_SHA1: ${ORDINARY_OWNERSHIP_PROJECTION_SHA1:-}
      ORDINARY_GO_METRICS_ADDRESS: 127.0.0.1:9104
      DISCOVERY_CONCURRENCY: "1"
      MONITOR_CONCURRENCY: "1"
      SHUTDOWN_GRACE_SECONDS: "1"
    healthcheck:
      test: [CMD, /usr/local/bin/go-ordinary-worker, --health]
      interval: 1s
      timeout: 3s
      retries: 5
`, filepath.Dir(f.r.Options().Addr)+":"+filepath.Dir(f.r.Options().Addr), dsn, "unix://"+f.r.Options().Addr)
			write("docker-compose.yml", compose.String())
			write("lightpanda-b0-enabled.override.yml", "services: {}\n")
			t.Cleanup(func() {
				_, _ = command("docker", "compose", "--profile", "ordinary-go", "down", "--remove-orphans", "--volumes")
			})
			rendered := must("docker", "compose", "config")
			write(".lightpanda-b0-active-v1", fmt.Sprintf("schema=jobseek.lightpanda-b0-active/v1\nstate=active\ncohort=c1\nnamespace=production-b0\nshard_id=lightpanda-b0\nrouting_epoch=%d\nplan_digest=%s\ncompose_digest=%x\ncrawler_image_ref=%s\ndeploy_revision=%s\nactivated_at_epoch=1\n", plan.Epoch(), strings.Repeat("d", 64), sha256.Sum256(rendered), ref, plan.SourceRevision()))
			body, err := os.ReadFile("../../scripts/ordinary-go-cutover.sh")
			if err != nil {
				t.Fatal(err)
			}
			wrapper := string(body)
			for old, next := range map[string]string{
				"DEPLOY_DIR=/home/deploy":                      "DEPLOY_DIR=" + dir,
				"LOCK=/run/lock/jobseek-crawler-mutation.lock": "LOCK=" + filepath.Join(dir, "mutation.lock"),
				`"$(id -un)" == deploy`:                        `"$(id -un)" == "$(id -un)"`,
				"expected_image=$CRAWLER_IMAGE_REF":            "expected_image=$FIXTURE_IMAGE_ID",
				"expected_image=$BROWSER_IMAGE_REF":            "expected_image=$FIXTURE_IMAGE_ID",
			} {
				if !strings.Contains(wrapper, old) {
					t.Fatal("host fixture replacement no longer matches the production driver")
				}
				wrapper = strings.ReplaceAll(wrapper, old, next)
			}
			write("cutover.sh", wrapper)
			// Keep the actual Go process idle while host lifecycle changes occur.
			// Its health is real; an interrupted claim is inserted only after it
			// is stopped, using the normal native authority against this fixture.
			due := time.Now().UTC().Add(time.Hour).Truncate(time.Microsecond)
			if _, err := f.pg.Exec(ctx, "UPDATE job_board SET next_check_at=$2,tdm_reserved=true WHERE id=$1::uuid", f.board, due); err != nil {
				t.Fatal(err)
			}
			if err := f.r.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: float64(due.UnixMicro()) / 1e6, Member: f.board}).Err(); err != nil {
				t.Fatal(err)
			}
			must("docker", "compose", "up", "-d")
			if mode == "readiness-failure" {
				write("fail-health", "private injected endpoint failure\n")
			}
			out, activationErr := command("bash", "cutover.sh", "activate", plan.SHA256(), plan.ProjectionSHA1())
			if mode == "readiness-failure" {
				if activationErr == nil {
					t.Fatal("failed readiness admitted active restart policies")
				}
				for service := range ports {
					id := strings.TrimSpace(string(must("docker", "compose", "ps", "-aq", service)))
					if got := strings.TrimSpace(string(must("docker", "inspect", "-f", "{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}", id))); got != "false:no" {
						t.Fatal("failure left a writer armed or running", service, got)
					}
				}
				id := strings.TrimSpace(string(must("docker", "compose", "--profile", "ordinary-go", "ps", "-aq", "ordinary-go")))
				if got := strings.TrimSpace(string(must("docker", "inspect", "-f", "{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}", id))); got != "false:no" {
					t.Fatal("failure left native owner running", got)
				}
				if err := os.Remove(filepath.Join(dir, "fail-health")); err != nil {
					t.Fatal(err)
				}
				must("bash", "cutover.sh", "recover-pending")
			} else {
				if activationErr != nil {
					t.Fatal("physical cutover failed", activationErr, strings.ReplaceAll(string(out), dsn, "[private fixture database]"))
				}
				must("docker", "compose", "--profile", "ordinary-go", "stop", "ordinary-go")
				if err := f.r.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: 1, Member: f.board}).Err(); err != nil {
					t.Fatal(err)
				}
				native, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, plan.Epoch(), plan.SHA256(), plan.SourceRevision())
				if err != nil {
					t.Fatal(err)
				}
				defer native.Close()
				if claim, err := native.Claim(ctx, queue.Simple); err != nil || claim == nil {
					t.Fatal("interrupted real-host claim unavailable", err)
				}
				var receiptBefore string
				if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&receiptBefore); err != nil {
					t.Fatal(err)
				}
				must("bash", "cutover.sh", "retire")
				var receiptAfter string
				if err := f.pg.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_id=$1::uuid", f.board).Scan(&receiptAfter); err != nil || receiptBefore != receiptAfter {
					t.Fatal("physical recovery changed an interrupted receipt", err)
				}
			}
			if _, err := os.Stat(filepath.Join(dir, ".ordinary-go-owner-v1")); !os.IsNotExist(err) {
				t.Fatal("successful recovery retained a pending owner")
			}
			var state string
			if err := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.SHA256()).Scan(&state); err != nil || state != "retired" {
				t.Fatal("physical recovery did not retire SQL ownership", err)
			}
			if f.r.Exists(ctx, "ordinary:ownership:active").Val() != 0 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
				t.Fatal("physical recovery left native authority")
			}
			if score := f.r.ZScore(ctx, "monitors_simple:greenhouse", f.board).Val(); score != float64(due.UnixMicro())/1e6 {
				t.Fatal("physical recovery lost the canonical deadline", score)
			}
			for service := range ports {
				id := strings.TrimSpace(string(must("docker", "compose", "ps", "-q", service)))
				if got := strings.TrimSpace(string(must("docker", "inspect", "-f", "{{.State.Running}}:{{.HostConfig.RestartPolicy.Name}}", id))); got != "true:unless-stopped" {
					t.Fatal("recovered legacy stack not ready and armed", service, got)
				}
			}
			var epoch int64
			if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil || epoch != plan.Epoch() {
				t.Fatal("physical recovery changed the B0 epoch")
			}
			t.Log("installed native image, actual container cold window and full stack recovery passed", mode)
		})
	}
}
