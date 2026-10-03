//go:build integration

package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// No local agent Docker access. This harness uses the image-installed worker on
// the existing disposable Actions host and its independently pulled PostgreSQL
// image. Runtime labels and requested generations are synthetic; no cold grant.
func TestActualInstalledNativeHostPreflightRetainsBoundObservations(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_PREFLIGHT") != "1" {
		t.Skip("explicit disposable installed native host fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("host preflight requires disposable Linux installed-image harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	source := ordinaryFixtureSourceRevision(t)
	g, files := releaseExecutableGeneration(t, source)
	deployment := hostPrivateDirectory(t)
	g = hostSelectActiveFixture(t, deployment, g)
	const ref = "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
	compose := []byte("services:\n  postgres:\n    image: " + ref + "\n    environment:\n      PRIVATE_VALUE: ${OPAQUE_OPERATOR_VALUE}\n")
	old := hostDigest([]byte(files["docker-compose.yml"]))
	for name, body := range map[string][]byte{"docker-compose.yml": compose, "docker-compose.sha256": []byte(hostDigest(compose) + "\n"), "release.manifest": []byte(strings.ReplaceAll(files["release.manifest"], old, hostDigest(compose)))} {
		if os.WriteFile(filepath.Join(g, name), body, 0600) != nil {
			t.Fatal("host generation fixture")
		}
	}
	f, err := release.VerifyFiles(ctx, g, "colophon-group")
	if err != nil {
		t.Fatal("host generation validation", err)
	}
	state := hostPrivateDirectory(t)
	for name, body := range map[string]string{"docker-compose.yml": "services: {}\n", "deploy.sh": "#!/usr/bin/env bash\nexit 0\n", "alloy.river": "fixture-only\n"} {
		if os.WriteFile(filepath.Join(deployment, name), []byte(body), 0600) != nil {
			t.Fatal("deployment spec fixture")
		}
	}
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: source, Owner: "colophon-group", Project: "jobseek-native-host-preflight", Architecture: runtime.GOARCH, DeploymentDirectory: deployment, Installed: []HostInstalledRequest{}}
	for _, role := range []string{"active", "incoming", "rollback"} {
		r.Releases = append(r.Releases, HostReleaseRequest{role, g, f.SHA256()})
	}
	body, _ := json.Marshal(r)
	if os.WriteFile(filepath.Join(state, "request.json"), body, 0600) != nil {
		t.Fatal("protected host request fixture")
	}
	call := func(accept bool) *HostPreflightResult {
		t.Helper()
		cmd := exec.CommandContext(ctx, os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY"), "--host-preflight")
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=host-preflight", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION=" + source, "ORDINARY_HOST_REQUEST_DIRECTORY=" + state, "ORDINARY_HOST_REQUEST_SHA256=" + hostDigest(body), "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "DOCKER_HOST=tcp://127.0.0.1:1", "DOCKER_CONFIG=/nonexistent-fixture", "OPAQUE_OPERATOR_VALUE=caller-sensitive-value"}
		out, err := cmd.CombinedOutput()
		if bytes.Contains(out, []byte(state)) || bytes.Contains(out, []byte(g)) || bytes.Contains(out, []byte("fixture-sensitive-value")) || bytes.Contains(out, []byte("caller-sensitive-value")) {
			t.Fatal("host preflight disclosed private inputs")
		}
		if !accept {
			if err == nil || strings.TrimSpace(string(out)) != "ordinary host preflight rejected" || ctx.Err() != nil {
				t.Fatal("actual host preflight admitted drift", err)
			}
			return nil
		}
		var got HostPreflightResult
		if err != nil || json.Unmarshal(out, &got) != nil || got.SourceRevision != source || got.Operation != "host-preflight" || got.Phase != "preflight_retained" || got.RuntimeAdmission || got.RequestSHA256 != hostDigest(body) || got.ReceiptSHA256 != hostDigest(got.Receipt) {
			t.Fatal("actual native host preflight rejected/lost identity", err)
		}
		retained, e := os.ReadFile(filepath.Join(state, "preflight-"+got.ReceiptSHA256+".json"))
		if e != nil || !bytes.Equal(retained, got.Receipt) {
			t.Fatal("host completion printed before exact durable receipt")
		}
		return &got
	}
	first := call(true)
	var receipt struct {
		Selection struct {
			FilesSHA         string `json:"file_evidence_sha256"`
			Source           string `json:"selected_deploy_revision"`
			RuntimeAdmission bool   `json:"runtime_admission"`
		} `json:"selected_active"`
	}
	if json.Unmarshal(first.Receipt, &receipt) != nil || receipt.Selection.FilesSHA != f.SHA256() || receipt.Selection.Source != source || receipt.Selection.RuntimeAdmission {
		t.Fatal("host preflight lost actual selected active file binding")
	}
	retry := call(true)
	if first.IntentSHA256 != retry.IntentSHA256 || first.ReceiptSHA256 != retry.ReceiptSHA256 {
		t.Fatal("exact actual host retry adopted different evidence")
	}
	intent, err := os.ReadFile(filepath.Join(state, "intent.json"))
	if err != nil || hostDigest(intent) != first.IntentSHA256 {
		t.Fatal("host intent binding")
	}
	var boundIntent struct {
		SelectionSHA string `json:"selected_active_sha256"`
	}
	var rawReceipt struct {
		Selection json.RawMessage `json:"selected_active"`
	}
	if json.Unmarshal(intent, &boundIntent) != nil || json.Unmarshal(first.Receipt, &rawReceipt) != nil || boundIntent.SelectionSHA != hostDigest(rawReceipt.Selection) {
		t.Fatal("host intent did not retain the exact selected active observation")
	}
	archive, err := os.ReadFile(filepath.Join(state, "deploy-specs.tar"))
	if err != nil {
		t.Fatal("host archive retention")
	}
	if _, err := release.DecodeSpecArchive(ctx, archive, hostDigest(archive)); err != nil {
		t.Fatal("host archive framing")
	}
	if os.WriteFile(filepath.Join(deployment, "alloy.river"), []byte("changed spec\n"), 0600) != nil {
		t.Fatal("spec drift fixture")
	}
	call(false)
	if os.WriteFile(filepath.Join(deployment, "alloy.river"), []byte("fixture-only\n"), 0600) != nil {
		t.Fatal("spec restoration fixture")
	}
	if os.WriteFile(filepath.Join(g, "data/companies.csv"), []byte("changed data\n"), 0600) != nil {
		t.Fatal("generation drift fixture")
	}
	call(false)
	for name, want := range map[string][]byte{"intent.json": intent, "deploy-specs.tar": archive} {
		got, e := os.ReadFile(filepath.Join(state, name))
		if e != nil || !bytes.Equal(got, want) {
			t.Fatal("failed preflight overwrote retained rollback evidence")
		}
	}
	t.Log("actual installed native host preflight held shared mutation lock, joined three requested file/image generations and complete inventory/declared execution, retained bound intent before exact spec archive and immutable receipt, recovered exact retry and refused spec/data drift; public infrastructure/synthetic release fixture; runtime admission false")
	t.Log("actual installed host preflight bound selected active pointer/live success marker and complete selection readback to retained intent/receipt; fixture selection only; runtime admission false")
}

func hostSelectActiveFixture(t *testing.T, deployment, g string) string {
	t.Helper()
	root := filepath.Join(deployment, ".crawler-release-generations")
	target := filepath.Join(root, "release-active")
	if os.Mkdir(root, 0700) != nil || os.Rename(g, target) != nil || os.Symlink(target, filepath.Join(deployment, ".crawler-active-release")) != nil || os.Link(filepath.Join(target, "success.env"), filepath.Join(deployment, ".crawler-deploy-success.env")) != nil {
		t.Fatal("actual selected generation/pointer/success fixture")
	}
	return target
}
