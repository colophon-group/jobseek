package worker

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

func TestSpecCaptureInputsRequireBoundReleaseAndCompiledSource(t *testing.T) {
	env := map[string]string{"ORDINARY_DEPLOY_SPEC_DIRECTORY": "/private/deployment", "ORDINARY_RELEASE_GENERATION_DIRECTORY": "/private/generation", "ORDINARY_RELEASE_OWNER": "colophon-group", "ORDINARY_RELEASE_FILES_SHA256": strings.Repeat("a", 64), "ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH": "/private/rollback.tar"}
	get := func(k string) string { return env[k] }
	source := strings.Repeat("b", 40)
	c, err := ReadReleaseSpecsConfig(get, source)
	if err != nil || c.source != source {
		t.Fatal("bound capture inputs refused")
	}
	for _, key := range []string{"ORDINARY_DEPLOY_SPEC_DIRECTORY", "ORDINARY_RELEASE_GENERATION_DIRECTORY", "ORDINARY_RELEASE_OWNER", "ORDINARY_RELEASE_FILES_SHA256", "ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH", "ORDINARY_DEPLOY_SPEC_ARCHIVE_SHA256", "ORDINARY_DEPLOY_SPEC_CAPTURE_SHA256"} {
		old := env[key]
		env[key] = "invalid"
		if key == "ORDINARY_RELEASE_OWNER" {
			env[key] = "invalid/owner"
		}
		if _, err := ReadReleaseSpecsConfig(get, source); !errors.Is(err, errReleaseSpecs) {
			t.Fatal("unbound capture input accepted", key)
		}
		env[key] = old
	}
	if _, err := ReadReleaseSpecsConfig(get, "invalid"); !errors.Is(err, errReleaseSpecs) {
		t.Fatal("invalid compiled source")
	}
	if _, err := ReadReleaseSpecsConfig(nil, source); !errors.Is(err, errReleaseSpecs) {
		t.Fatal("nil capture environment")
	}
	env["ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH"] = "/private/generation/rollback.tar"
	if _, err := ReadReleaseSpecsConfig(get, source); !errors.Is(err, errReleaseSpecs) {
		t.Fatal("archive would invalidate its committed generation")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := RunReleaseSpecs(ctx, c); !errors.Is(err, errReleaseSpecs) {
		t.Fatal("cancelled capture")
	}
}

func TestRealNativeExecutableCapturesRetainedDeploymentSpecsWithoutRuntimeAuthority(t *testing.T) {
	source := ordinaryFixtureSourceRevision(t)
	binary := os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY")
	if binary == "" {
		binary = filepath.Join(t.TempDir(), "ordinary-worker")
		cmd := exec.Command("go", "build", "-buildvcs=false", "-ldflags=-X main.sourceRevision="+source, "-o", binary, "./cmd/live")
		if err := cmd.Run(); err != nil {
			t.Fatal("spec capture fixture build failed")
		}
	}
	generation, files := releaseExecutableGeneration(t, source)
	f, err := release.VerifyFiles(context.Background(), generation, "colophon-group")
	if err != nil {
		t.Fatal(err)
	}
	deployment, retained := t.TempDir(), t.TempDir()
	if os.Chmod(deployment, 0700) != nil || os.Chmod(retained, 0700) != nil || os.Mkdir(filepath.Join(deployment, "scripts"), 0755) != nil {
		t.Fatal("private spec fixture")
	}
	// Omit the old bridge verifier and enabled override, matching a first rollout.
	names := []string{"deploy.sh", "deploy_helpers.sh", "docker-compose.yml", "alloy.river", "scripts/postgresql-operational-preflight.py", "scripts/lightpanda-claimant-credentials.py", "scripts/lightpanda-b0-cutover.sh"}
	for _, name := range names {
		if os.WriteFile(filepath.Join(deployment, name), []byte("old "+name+" fixture-sensitive-value\n"), 0755) != nil {
			t.Fatal("active spec fixture")
		}
	}
	archive := filepath.Join(retained, "rollback.tar")
	env := []string{"ORDINARY_DEPLOY_SPEC_DIRECTORY=" + deployment, "ORDINARY_RELEASE_GENERATION_DIRECTORY=" + generation, "ORDINARY_RELEASE_OWNER=colophon-group", "ORDINARY_RELEASE_FILES_SHA256=" + f.SHA256(), "ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH=" + archive, "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "ORDINARY_GO_WORKER_MODE=enabled"}
	sha := func(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
	call := func(extra []string, accepted bool) *ReleaseSpecsResult {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, binary, "--capture-deploy-specs")
		cmd.Env = append(append([]string{}, env...), extra...)
		b, err := cmd.CombinedOutput()
		for _, private := range []string{"fixture-sensitive-value", deployment, generation, retained} {
			if strings.Contains(string(b), private) {
				t.Fatal("spec capture command disclosed protected data")
			}
		}
		if !accepted {
			if err == nil || strings.TrimSpace(string(b)) != "ordinary release spec capture rejected" {
				t.Fatal("actual spec capture command accepted drift")
			}
			return nil
		}
		var r ReleaseSpecsResult
		if err != nil || json.Unmarshal(b, &r) != nil || r.SourceRevision != source || r.Operation != "capture-deploy-specs" || r.Phase != "spec_archive_retained" || r.FileEvidenceSHA256 != f.SHA256() || r.CaptureEvidenceSHA256 != sha(r.CaptureEvidence) {
			t.Fatal("actual spec command lost canonical identity", err)
		}
		return &r
	}
	call([]string{"ORDINARY_DEPLOY_SPEC_ARCHIVE_SHA256=" + strings.Repeat("0", 64)}, false)
	if _, err := os.Lstat(archive); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("mismatched protected intent published an archive")
	}
	r := call(nil, true)
	before, err := os.Lstat(archive)
	if err != nil || before.Mode().Perm() != 0600 {
		t.Fatal("archive was not privately retained")
	}
	b, err := os.ReadFile(archive)
	if err != nil || sha(b) != r.ArchiveSHA256 {
		t.Fatal("retained archive bytes differ from receipt")
	}
	if _, err := release.DecodeSpecArchive(context.Background(), b, r.ArchiveSHA256); err != nil {
		t.Fatal(err)
	}
	reader := tar.NewReader(bytes.NewReader(b))
	seenCompose, seenAbsent := false, false
	for {
		h, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		body, err := io.ReadAll(reader)
		if err != nil {
			t.Fatal(err)
		}
		if h.Name == "docker-compose.yml" {
			seenCompose = true
			if string(body) != files[h.Name] || h.Mode != 0644 {
				t.Fatal("archive used mutable live Compose")
			}
		}
		if h.Name == ".jobseek-deploy-spec-presence-v1" {
			seenAbsent = strings.Contains(string(body), "ABSENT - - scripts/verify-crawler-release-bridge.py\n") && strings.Contains(string(body), "ABSENT - - lightpanda-b0-enabled.override.yml\n")
		}
	}
	if !seenCompose || !seenAbsent {
		t.Fatal("first rollout rollback archive lost Compose or absence")
	}
	bound := call([]string{"ORDINARY_DEPLOY_SPEC_ARCHIVE_SHA256=" + r.ArchiveSHA256, "ORDINARY_DEPLOY_SPEC_CAPTURE_SHA256=" + r.CaptureEvidenceSHA256}, true)
	after, err := os.Lstat(archive)
	if err != nil || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) || !bytes.Equal(r.CaptureEvidence, bound.CaptureEvidence) {
		t.Fatal("exact capture retry replaced evidence")
	}
	call([]string{"ORDINARY_DEPLOY_SPEC_CAPTURE_SHA256=" + strings.Repeat("0", 64)}, false)
	if os.WriteFile(filepath.Join(deployment, "deploy.sh"), []byte("changed"), 0755) != nil {
		t.Fatal("drift fixture")
	}
	call([]string{"ORDINARY_DEPLOY_SPEC_ARCHIVE_SHA256=" + r.ArchiveSHA256}, false)
	if unchanged, err := os.ReadFile(archive); err != nil || !bytes.Equal(unchanged, b) {
		t.Fatal("active spec drift overwrote retained rollback archive")
	}
	// A lexical alias to the committed generation must also refuse before any
	// archive is created, even though the alias itself is outside that path.
	alias := filepath.Join(t.TempDir(), "generation-alias")
	if os.Symlink(generation, alias) != nil {
		t.Fatal("alias fixture")
	}
	call([]string{"ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH=" + filepath.Join(alias, "alias.tar")}, false)
	if _, err := os.Lstat(filepath.Join(generation, "alias.tar")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("capture mutated its generation through an alias")
	}
	for name, body := range files {
		got, err := os.ReadFile(filepath.Join(generation, name))
		if err != nil || string(got) != body {
			t.Fatal("capture changed committed release")
		}
	}
}
