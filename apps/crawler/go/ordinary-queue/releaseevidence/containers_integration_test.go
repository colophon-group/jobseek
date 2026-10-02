//go:build integration

package releaseevidence

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// Only the existing disposable Actions harness owns this daemon. The agent must
// never run this test locally. Its already-pulled public PostgreSQL image runs a
// bounded sleep as a synthetic exporter; it does not represent runtime fidelity.
func TestActualWholeDaemonContainerStateAndRestartObservation(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_DOCKER_OBSERVATION") != "1" {
		t.Skip("explicit disposable CI container-observation fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" {
		t.Fatal("container observation requires the disposable Linux Actions harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	const ref = "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
	project := fmt.Sprintf("jobseek-container-observation-%d", os.Getpid())
	g := fixture(t)
	deploymentDir := t.TempDir()
	mountDir := filepath.Join(deploymentDir, "fixture-config")
	if err := os.Mkdir(mountDir, 0700); err != nil {
		t.Fatal(err)
	}
	compose := "services:\n  exporter:\n    image: " + ref + "\n    user: '0:0'\n    working_dir: /tmp\n    entrypoint: ['/bin/sleep']\n    command: ['75']\n    environment:\n      PRIVATE_VALUE: fixture-sensitive-value\n    volumes:\n      - type: bind\n        source: ./fixture-config\n        target: /fixture-config\n        read_only: true\n"
	write(t, g, "docker-compose.yml", []byte(compose))
	refresh(t, g)
	f := verify(t, g)
	proof, err := ObserveImages(ctx, ImageObservationConfig{Directory: g.directory, Owner: fixtureOwner, Project: project, FileEvidenceSHA256: f.SHA256(), Architecture: runtime.GOARCH, ProjectDirectory: deploymentDir})
	if err != nil {
		t.Fatal(err)
	}
	// Fixed commands affect only a newly created disposable fixture. No pull,
	// production access, arbitrary shell or daemon-wide mutation is permitted.
	b, err := readDocker(ctx, []string{"create", "--pull=never", "--restart=always", "--label", "com.docker.compose.project=" + project, "--label", "com.docker.compose.service=exporter", "--label", "com.docker.compose.oneoff=False", "--user", "0:0", "--workdir", "/tmp", "--mount", "type=bind,source=" + mountDir + ",target=/fixture-config,readonly", "--env", "PRIVATE_VALUE=fixture-sensitive-value", "--entrypoint", "/bin/sleep", ref, "75"})
	if err != nil {
		t.Fatal("create disposable exporter fixture", err)
	}
	id := strings.TrimSpace(string(b))
	if !containerIDPattern.MatchString(id) {
		t.Fatal("invalid fixture container identity")
	}
	t.Cleanup(func() {
		cleanup, done := context.WithTimeout(context.Background(), 20*time.Second)
		defer done()
		if _, err := readDocker(cleanup, []string{"rm", "--force", id}); err != nil {
			t.Error("remove only owned disposable container", err)
		}
	})
	check := func(running bool, policy string) {
		t.Helper()
		inventory, err := ObserveContainers(ctx)
		if err != nil {
			t.Fatal(err)
		}
		found, foreignRunning := false, false
		for _, row := range inventory.rows {
			if row.ID == id {
				found = true
				if row.Project != project || row.Service != "exporter" || row.Running != running || row.RestartPolicy != policy || row.Paused || row.Restarting || (running && (row.PID <= 0 || row.Status != "running")) || (!running && row.PID != 0) {
					t.Fatal("actual exporter state/restart observation differs")
				}
			} else if row.Running && row.Project != project {
				foreignRunning = true // includes this job's independent PostgreSQL
			}
		}
		if !found || !foreignRunning {
			t.Fatal("whole-daemon enumeration omitted controlled exporter or independent live service")
		}
		if strings.Contains(inventory.Body(), "fixture-sensitive-value") || strings.Contains(inventory.Body(), "PRIVATE_VALUE") {
			t.Fatal("container receipt disclosed environment")
		}
		if _, err := RequireColdContainers(ctx, inventory, []*Images{proof}); !errors.Is(err, ErrInvalid) {
			t.Fatal("unaccounted globally live service falsely admitted as cold", err)
		}
		execution, err := RequireContainerExecution(ctx, inventory, []*Images{proof})
		if err != nil {
			t.Fatal("actual release execution settings differ", err)
		}
		if strings.Contains(execution.Body(), "fixture-sensitive-value") || strings.Contains(execution.Body(), mountDir) {
			t.Fatal("execution receipt disclosed private inputs")
		}
	}
	check(false, "always")
	if _, err := readDocker(ctx, []string{"start", id}); err != nil {
		t.Fatal("start disposable fixture", err)
	}
	check(true, "always")
	if _, err := readDocker(ctx, []string{"stop", "--time", "1", id}); err != nil {
		t.Fatal("stop disposable fixture", err)
	}
	check(false, "always") // stopped alone is not restart exclusion
	if _, err := readDocker(ctx, []string{"update", "--restart=no", id}); err != nil {
		t.Fatal("disable only fixture restart policy", err)
	}
	check(false, "no")
	inventory, err := ObserveContainers(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, drift := range []string{"user", "environment", "command", "bind source", "bind access"} {
		changed := compose
		switch drift {
		case "user":
			changed = strings.Replace(changed, "'0:0'", "'10001:10001'", 1)
		case "environment":
			changed = strings.Replace(changed, "PRIVATE_VALUE: fixture-sensitive-value", "PRIVATE_VALUE: changed-fixture-value", 1)
		case "command":
			changed = strings.Replace(changed, "command: ['75']", "command: ['74']", 1)
		case "bind source":
			changed = strings.Replace(changed, "source: ./fixture-config", "source: ./other-fixture", 1)
		case "bind access":
			changed = strings.Replace(changed, "read_only: true", "read_only: false", 1)
		}
		write(t, g, "docker-compose.yml", []byte(changed))
		refresh(t, g)
		f := verify(t, g)
		mismatch, err := ObserveImages(ctx, ImageObservationConfig{Directory: g.directory, Owner: fixtureOwner, Project: project, FileEvidenceSHA256: f.SHA256(), Architecture: runtime.GOARCH, ProjectDirectory: deploymentDir})
		if err != nil {
			t.Fatal("observe declared drift", err)
		}
		if _, err := RequireContainerExecution(ctx, inventory, []*Images{mismatch}); !errors.Is(err, ErrInvalid) {
			t.Fatal("actual execution drift admitted", drift, err)
		}
	}
	t.Log("actual release-bound command/environment/numeric configured user/bind/image-volume execution verified; declared drift refused; no source/content/process-identity admission")
	t.Log("actual whole-daemon inventory verified created/running/stopped exporter and automatic restart exclusion; independent live PostgreSQL prevents cold receipt; synthetic commands, no complete host admission")
}
