//go:build integration

package releaseevidence

import (
	"context"
	"encoding/json"
	"os"
	"runtime"
	"strings"
	"testing"
)

// This existing disposable Actions harness owns its daemon. Local agent tests
// must never invoke this path or receive the Docker socket. The public image is
// already independently pulled by the job's PostgreSQL service, not by this test.
func TestActualClearedComposeAndLocalImageObservation(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_DOCKER_OBSERVATION") != "1" {
		t.Skip("explicit disposable CI image-observation fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" {
		t.Fatal("image observation requires the disposable Linux Actions harness")
	}
	g := fixture(t)
	const ref = "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
	write(t, g, "docker-compose.yml", []byte("services:\n  postgres:\n    image: "+ref+"\n    environment:\n      PRIVATE_VALUE: ${OPAQUE_OPERATOR_VALUE}\n"))
	refresh(t, g)
	f := verify(t, g)
	// A caller's daemon, context, project and interpolation cannot redirect the
	// actual fixed command. No host/container mutation is permitted in this test.
	t.Setenv("DOCKER_HOST", "tcp://127.0.0.1:1")
	t.Setenv("DOCKER_CONTEXT", "nonexistent-fixture-context")
	t.Setenv("DOCKER_CONFIG", "/nonexistent-fixture-directory")
	t.Setenv("COMPOSE_PROJECT_NAME", "caller-project-drift")
	t.Setenv("COMPOSE_FILE", "/nonexistent-compose-file")
	t.Setenv("OPAQUE_OPERATOR_VALUE", "caller-sensitive-value")
	c := ImageObservationConfig{Directory: g.directory, Owner: fixtureOwner, Project: "jobseek-release-observation", FileEvidenceSHA256: f.SHA256(), Architecture: runtime.GOARCH}
	observed, err := ObserveImages(context.Background(), c)
	if err != nil {
		t.Fatal(err)
	}
	var d imageDocument
	if json.Unmarshal([]byte(observed.Body()), &d) != nil || d.Services["postgres"].Reference != ref || !strings.HasPrefix(d.Services["postgres"].ImageID, "sha256:") || d.FileEvidenceSHA256 != f.SHA256() || d.Architecture != runtime.GOARCH {
		t.Fatal("actual image observation binding differs")
	}
	if strings.Contains(observed.Body(), "fixture-sensitive-value") || strings.Contains(observed.Body(), "caller-sensitive-value") || strings.Contains(observed.Body(), g.directory) {
		t.Fatal("actual observation disclosed private inputs")
	}
	if verify(t, g).SHA256() != f.SHA256() {
		t.Fatal("read-only observation changed the release")
	}
	t.Log("actual cleared-environment Compose/local image observation verified; public infrastructure fixture, synthetic runtime labels; no release admission")
}
