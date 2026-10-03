package worker

import (
	"bufio"
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"
)

const firstOwnershipFixtureHealthScript = `import http.server, pathlib, sys, time
port = int(sys.argv[1])
fail_gate = sys.argv[3] == 'true'
print('starting', flush=True)
time.sleep(float(sys.argv[2]))
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503 if fail_gate and pathlib.Path(__file__).with_name('fail-health').exists() else 204)
        self.end_headers()
    def log_message(self, *args): pass
http.server.HTTPServer(('127.0.0.1', port), Handler).serve_forever()
`

func firstOwnershipFixtureListenerProbe(port int) []string {
	return []string{"CMD", "/app/.venv/bin/python", "-c", "import socket, sys; socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=1).close()", strconv.Itoa(port)}
}

// Export only bounded state counts; never inspect environment or health logs.
func firstOwnershipFixtureContainerStates(dir, project string) map[string]int {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	run := func(args ...string) ([]byte, error) {
		cmd := exec.CommandContext(ctx, "docker", args...)
		cmd.Dir = dir
		cmd.Env = append(os.Environ(), "COMPOSE_PROJECT_NAME="+project)
		return cmd.Output()
	}
	out, err := run("compose", "--profile", "ordinary-go", "ps", "-aq")
	if err != nil {
		return map[string]int{"unavailable": 1}
	}
	ids := strings.Fields(string(out))
	if len(ids) == 0 || len(ids) > 20 {
		return map[string]int{"missing_or_excessive": 1}
	}
	for _, id := range ids {
		if !planPattern.MatchString(id) {
			return map[string]int{"invalid_identity": 1}
		}
	}
	args := []string{"inspect", "-f", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.State.ExitCode}}|{{.HostConfig.RestartPolicy.Name}}"}
	out, err = run(append(args, ids...)...)
	if err != nil {
		return map[string]int{"unavailable": 1}
	}
	counts := make(map[string]int)
	for _, row := range strings.Fields(string(out)) {
		fields := strings.Split(row, "|")
		if len(fields) != 4 || !strings.Contains("|created|running|paused|restarting|removing|exited|dead|", "|"+fields[0]+"|") || !strings.Contains("|none|starting|healthy|unhealthy|", "|"+fields[1]+"|") || !strings.Contains("|no|always|unless-stopped|on-failure|", "|"+fields[3]+"|") {
			counts["unknown_state"]++
			continue
		}
		exit, err := strconv.Atoi(fields[2])
		if err != nil || exit < 0 || exit > 255 {
			counts["unknown_state"]++
			continue
		}
		counts[fmt.Sprintf("%s/health=%s/exit=%d/restart=%s", fields[0], fields[1], exit, fields[3])]++
	}
	return counts
}

// This name runs in CI's existing executable proof step, before the host test.
// It needs no database, Redis, container runtime or Docker socket.
func TestRealFirstOwnershipExecutableFixtureListenerReadiness(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required fixture readiness proof needs Python")
		}
		t.Skip("Python is unavailable in the compile-only image stage")
	}
	curl, err := exec.LookPath("curl")
	if err != nil {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required fixture HTTP gate proof needs curl")
		}
		t.Skip("curl is unavailable in the compile-only image stage")
	}
	reservation, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := reservation.Addr().(*net.TCPAddr).Port
	if err := reservation.Close(); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	script := filepath.Join(dir, "health.py")
	if err := os.WriteFile(script, []byte(firstOwnershipFixtureHealthScript), 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	process := exec.CommandContext(ctx, python, script, strconv.Itoa(port), "1", "true")
	stdout, err := process.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = process.Process.Kill(); _ = process.Wait() })
	ready := make(chan bool, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		ready <- scanner.Scan() && scanner.Text() == "starting"
	}()
	select {
	case started := <-ready:
		if !started {
			t.Fatal("fixture did not enter its delayed startup")
		}
	case <-ctx.Done():
		t.Fatal("fixture startup exceeded its bound")
	}
	probe := firstOwnershipFixtureListenerProbe(port)
	check := func() error {
		return exec.CommandContext(ctx, python, probe[2:]...).Run()
	}
	if err := check(); err == nil {
		t.Fatal("listener probe accepted a process before its delayed bind")
	}
	deadline := time.Now().Add(5 * time.Second)
	for check() != nil {
		if time.Now().After(deadline) {
			t.Fatal("fixture listener never became ready")
		}
		time.Sleep(20 * time.Millisecond)
	}
	url := fmt.Sprintf("http://127.0.0.1:%d/", port)
	client := &http.Client{Timeout: time.Second}
	response, err := client.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	_ = response.Body.Close()
	if response.StatusCode != http.StatusNoContent {
		t.Fatal("ready fixture failed its HTTP gate", response.StatusCode)
	}
	if err := os.WriteFile(filepath.Join(dir, "fail-health"), []byte("injected endpoint failure\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := check(); err != nil {
		t.Fatal("injected HTTP failure incorrectly disabled listener readiness")
	}
	err = exec.CommandContext(ctx, curl, "--silent", "--fail", "--max-time", "1", url).Run()
	if exit, ok := err.(*exec.ExitError); !ok || exit.ExitCode() != 22 {
		t.Fatal("unchanged curl --fail gate did not reject injected HTTP 503", err)
	}
	t.Log("running delayed fixture rejected until listener bound; TCP readiness preserved HTTP 503 rejection")
}
