package canary

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestProductionShadowWorkflowContract(t *testing.T) {
	path := filepath.Join("..", "..", "..", ".github", "workflows", "crawler-go-sitemap-shadow.yml")
	contents, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	workflow := string(contents)
	required := []string{
		"workflow_dispatch:",
		"group: deploy-murmur-shim",
		"environment: Production",
		"ref: main",
		"HETZNER_MURMUR_HOST",
		"HETZNER_MURMUR_KNOWN_HOSTS",
		"StrictHostKeyChecking=yes",
		"platforms: linux/arm64",
		"--read-only",
		"--network bridge",
		"--security-opt no-new-privileges:true",
		"--cap-drop ALL",
		"--pids-limit 64",
		"--cpus 0.5",
		"--memory 256m",
		"--memory-swap 256m",
		"protected_services_unchanged",
		".canonical_url_count > 0",
		".worker.max_in_flight == 2",
		".connections.waiters == 0",
		"jobseek-go-sitemap-shadow-auth.XXXXXX",
		"protected_before=",
	}
	for _, value := range required {
		if !strings.Contains(workflow, value) {
			t.Errorf("workflow is missing %q", value)
		}
	}
	for _, forbidden := range []string{"\n  push:", "\n  schedule:", "--network host", "StrictHostKeyChecking=accept-new", "--restart", "--env-file", "--volume"} {
		if strings.Contains(workflow, forbidden) {
			t.Errorf("workflow contains forbidden value %q", forbidden)
		}
	}
}

func TestShadowContainerIsStaticNonRootAndSourceAttested(t *testing.T) {
	contents, err := os.ReadFile(filepath.Join("..", "Dockerfile.shadow"))
	if err != nil {
		t.Fatal(err)
	}
	dockerfile := string(contents)
	for _, value := range []string{
		"golang:1.24.7-alpine3.22@sha256:",
		"FROM --platform=$BUILDPLATFORM",
		"ARG TARGETOS",
		"ARG TARGETARCH",
		`GOOS="$TARGETOS" GOARCH="$TARGETARCH"`,
		"FROM scratch",
		"USER 65532:65532",
		"-X main.sourceCommit=$SOURCE_COMMIT",
		"-X main.imageIdentity=$IMAGE_IDENTITY",
	} {
		if !strings.Contains(dockerfile, value) {
			t.Errorf("Dockerfile is missing %q", value)
		}
	}
}
