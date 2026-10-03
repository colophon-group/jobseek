package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestReleaseFileInputsRequireExplicitGenerationAndCompiledSource(t *testing.T) {
	env := map[string]string{"ORDINARY_RELEASE_GENERATION_DIRECTORY": "/private/generation", "ORDINARY_RELEASE_OWNER": "colophon-group"}
	get := func(key string) string { return env[key] }
	source := strings.Repeat("a", 40)
	c, err := ReadReleaseFilesConfig(get, source)
	if err != nil || c.expected != "" || c.source != source {
		t.Fatal("explicit observation refused")
	}
	for _, test := range []struct{ key, value string }{{"ORDINARY_RELEASE_GENERATION_DIRECTORY", "relative"}, {"ORDINARY_RELEASE_OWNER", "owner/escape"}, {"ORDINARY_RELEASE_FILES_SHA256", "bad"}} {
		old := env[test.key]
		env[test.key] = test.value
		if _, err := ReadReleaseFilesConfig(get, source); !errors.Is(err, errReleaseFiles) {
			t.Fatal("bad release input admitted", test.key)
		}
		env[test.key] = old
	}
	if _, err := ReadReleaseFilesConfig(get, "invalid"); !errors.Is(err, errReleaseFiles) {
		t.Fatal("invalid compiled source admitted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := RunReleaseFiles(ctx, c); !errors.Is(err, errReleaseFiles) {
		t.Fatal("cancelled observation admitted")
	}
}

// Uses the independently installed image binary when supplied by admission CI;
// otherwise it builds a clearly synthetic local-source executable fixture.
func TestRealNativeExecutableVerifiesReleaseFilesWithoutRuntimeAuthority(t *testing.T) {
	source := ordinaryFixtureSourceRevision(t)
	binary := os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY")
	if binary == "" {
		binary = filepath.Join(t.TempDir(), "ordinary-worker")
		cmd := exec.Command("go", "build", "-buildvcs=false", "-ldflags=-X main.sourceRevision="+source, "-o", binary, "./cmd/live")
		if err := cmd.Run(); err != nil {
			t.Fatal("release verifier fixture build failed")
		}
	}
	root, files := releaseExecutableGeneration(t, source)
	sha := func(b []byte) string { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
	env := []string{"ORDINARY_RELEASE_GENERATION_DIRECTORY=" + root, "ORDINARY_RELEASE_OWNER=colophon-group", "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "ORDINARY_GO_WORKER_MODE=enabled"}
	call := func(extra string, accepted bool) *ReleaseFilesResult {
		t.Helper()
		cmd := exec.Command(binary, "--verify-release-files")
		cmd.Env = append(append([]string{}, env...), extra)
		b, err := cmd.CombinedOutput()
		if strings.Contains(string(b), "fixture-sensitive-value") || strings.Contains(string(b), root) {
			t.Fatal("release command exposed protected environment or path")
		}
		if !accepted {
			if err == nil || strings.TrimSpace(string(b)) != "ordinary release file verification rejected" {
				t.Fatal("actual release command admitted drift")
			}
			return nil
		}
		var result ReleaseFilesResult
		if err != nil || json.Unmarshal(b, &result) != nil || result.Operation != "verify-release-files" || result.Phase != "files_verified" || result.SourceRevision != source || result.FileEvidenceSHA256 != sha(result.FileEvidence) {
			t.Fatal("actual release command lost canonical file identity")
		}
		return &result
	}
	observed := call("", true)
	bound := call("ORDINARY_RELEASE_FILES_SHA256="+observed.FileEvidenceSHA256, true)
	if string(observed.FileEvidence) != string(bound.FileEvidence) {
		t.Fatal("exact file observation retry drifted")
	}
	call("ORDINARY_RELEASE_FILES_SHA256="+strings.Repeat("0", 64), false)
	for name, body := range files {
		b, err := os.ReadFile(filepath.Join(root, name))
		if err != nil || string(b) != body {
			t.Fatal("read-only release command mutated evidence")
		}
	}
	if err := os.WriteFile(filepath.Join(root, "data/companies.csv"), []byte("changed"), 0600); err != nil {
		t.Fatal(err)
	}
	call("ORDINARY_RELEASE_FILES_SHA256="+observed.FileEvidenceSHA256, false)
}

func releaseExecutableGeneration(t *testing.T, source string) (string, map[string]string) {
	t.Helper()
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	sha := func(b []byte) string { v := sha256.Sum256(b); return hex.EncodeToString(v[:]) }
	files := map[string]string{
		"docker-compose.yml": "services:\n  worker:\n    image: ${CRAWLER_IMAGE_REF}\n",
		"environment.env":    "CRAWLER_IMAGE_TAG=fixture\nCRAWLER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64) + "\nBROWSER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + strings.Repeat("d", 64) + "\nJOBSEEK_DEPLOY_REVISION=" + source + "\nJOBSEEK_RUNTIME_CONTRACT_SHA256=" + strings.Repeat("e", 64) + "\n",
		"data/companies.csv": "slug,name\nfixture,Fixture\n",
	}
	files["success.env"] = files["environment.env"]
	files["environment.env"] += "OPAQUE_OPERATOR_VALUE=fixture-sensitive-value\n"
	files["data-files.sha256"] = sha([]byte(files["data/companies.csv"])) + "  companies.csv\n"
	files["docker-compose.sha256"] = sha([]byte(files["docker-compose.yml"])) + "\n"
	files["environment.sha256"] = sha([]byte(files["environment.env"])) + "\n"
	files["release.manifest"] = "RELEASE_FORMAT_VERSION=3\nCOMPOSE_SHA256=" + sha([]byte(files["docker-compose.yml"])) + "\nENVIRONMENT_SHA256=" + sha([]byte(files["environment.env"])) + "\nSUCCESS_SHA256=" + sha([]byte(files["success.env"])) + "\nDATA_FILES_SHA256=" + sha([]byte(files["data-files.sha256"])) + "\nDATA_CONTRACT_SHA256=" + sha([]byte(files["data-files.sha256"])) + "\nDATA_REVISION=" + source + "\nHAS_IMAGE_OVERRIDE=0\n"
	for name, body := range files {
		p := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(p), 0755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
	}
	return root, files
}
