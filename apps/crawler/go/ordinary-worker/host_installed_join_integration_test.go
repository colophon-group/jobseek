//go:build integration

package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// The disposable Actions harness owns a never-started, Compose-scoped native
// image container and its loopback registry. This test and the installed host
// command only observe Docker; neither creates or mutates Docker objects.
func TestActualInstalledNativeHostPreflightJoinsExactRuntimeAndRejectsSubstitution(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_INSTALLED_JOIN") != "1" {
		t.Skip("explicit disposable installed native host join fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("host installed join requires disposable Linux installed-image harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Second)
	defer cancel()
	file, err := os.Open(os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_JOIN_PROOF"))
	if err != nil {
		t.Fatal("host installed join proof open")
	}
	body, err := io.ReadAll(io.LimitReader(file, (1<<20)+1))
	file.Close()
	if err != nil || len(body) > 1<<20 {
		t.Fatal("bounded host installed join proof")
	}
	var proof struct {
		SourceRevision   string            `json:"source_revision"`
		ImageID          string            `json:"image_id"`
		Architecture     string            `json:"architecture"`
		BinarySHA256     string            `json:"binary_sha256"`
		CASHA256         string            `json:"system_ca_sha256"`
		Assets           map[string]string `json:"installed_asset_sha256"`
		Reference        string            `json:"immutable_reference"`
		ManifestSHA256   string            `json:"registry_manifest_sha256"`
		ManifestConfigID string            `json:"manifest_config_image_id"`
	}
	source := ordinaryFixtureSourceRevision(t)
	if json.Unmarshal(body, &proof) != nil || proof.SourceRevision != source || proof.Architecture != runtime.GOARCH || proof.ManifestConfigID != proof.ImageID || !planPattern.MatchString(proof.ManifestSHA256) || proof.Reference != "localhost:15000/native@sha256:"+proof.ManifestSHA256 {
		t.Fatal("host installed join source/platform/manifest identity")
	}
	expected := release.InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: source, ImageID: proof.ImageID, Architecture: proof.Architecture, Kind: "native-ordinary", BinarySHA256: proof.BinarySHA256, CASHA256: proof.CASHA256, Assets: proof.Assets}
	encoded, _ := json.Marshal(expected)
	if _, err := release.DecodeInstalledExpectation(encoded, hostDigest(encoded)); err != nil {
		t.Fatal("host installed join complete expectation")
	}
	if _, ok := proof.Assets["boards.csv"]; !ok || len(proof.Assets) != 34 {
		t.Fatal("host installed join complete image assets")
	}
	deployment := hostPrivateDirectory(t)
	if os.WriteFile(filepath.Join(deployment, "docker-compose.yml"), []byte("services: {}\n"), 0600) != nil {
		t.Fatal("host installed join deployment specs")
	}
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: source, Owner: "colophon-group", Project: "jobseek-native-installed-join", Architecture: runtime.GOARCH, DeploymentDirectory: deployment}
	for _, role := range []string{"active", "incoming", "rollback"} {
		g, files := releaseExecutableGeneration(t, source)
		compose := []byte("services:\n  worker:\n    image: " + proof.Reference + "\n    user: '10001:10001'\n")
		old := hostDigest([]byte(files["docker-compose.yml"]))
		for name, b := range map[string][]byte{"docker-compose.yml": compose, "docker-compose.sha256": []byte(hostDigest(compose) + "\n"), "release.manifest": []byte(strings.ReplaceAll(files["release.manifest"], old, hostDigest(compose)))} {
			if os.WriteFile(filepath.Join(g, name), b, 0600) != nil {
				t.Fatal("host installed join generation fixture")
			}
		}
		f, err := release.VerifyFiles(ctx, g, r.Owner)
		if err != nil {
			t.Fatal("host installed join generation verification")
		}
		r.Releases = append(r.Releases, HostReleaseRequest{role, g, f.SHA256()})
	}
	id := os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_JOIN_CONTAINER")
	r.Installed = []HostInstalledRequest{{Role: "active", Service: "worker", ContainerID: id, Expectation: expected}}
	call := func(t *testing.T, request HostPreflightRequest, accept bool) (*HostPreflightResult, string) {
		t.Helper()
		state := hostPrivateDirectory(t)
		b, err := json.Marshal(request)
		if err != nil || os.WriteFile(filepath.Join(state, "request.json"), b, 0600) != nil {
			t.Fatal("host installed join protected request")
		}
		cmd := exec.CommandContext(ctx, os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY"), "--host-preflight")
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=host-preflight", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION=" + source, "ORDINARY_HOST_REQUEST_DIRECTORY=" + state, "ORDINARY_HOST_REQUEST_SHA256=" + hostDigest(b), "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "DOCKER_HOST=tcp://127.0.0.1:1"}
		out, err := cmd.CombinedOutput()
		if bytes.Contains(out, []byte(state)) || bytes.Contains(out, []byte(deployment)) || bytes.Contains(out, []byte("fixture-sensitive-value")) {
			t.Fatal("host installed join disclosed private inputs")
		}
		if !accept {
			if err == nil || strings.TrimSpace(string(out)) != "ordinary host preflight rejected" || ctx.Err() != nil {
				t.Fatal("actual host installed join admitted substitution", err)
			}
			for _, name := range []string{"intent.json", "deploy-specs.tar"} {
				if _, e := os.Lstat(filepath.Join(state, name)); !os.IsNotExist(e) {
					t.Fatal("host installed join refusal retained unverified intent/archive")
				}
			}
			return nil, state
		}
		var got HostPreflightResult
		if err != nil || json.Unmarshal(out, &got) != nil || got.SourceRevision != source || got.Phase != "preflight_retained" || got.RuntimeAdmission || got.ReceiptSHA256 != hostDigest(got.Receipt) {
			t.Fatal("actual host installed join command", err)
		}
		retained, e := os.ReadFile(filepath.Join(state, "preflight-"+got.ReceiptSHA256+".json"))
		if e != nil || !bytes.Equal(retained, got.Receipt) {
			t.Fatal("host installed join completion before exact receipt retention")
		}
		return &got, state
	}
	got, state := call(t, r, true)
	var receipt struct {
		Installed []struct {
			ID          string `json:"container_id"`
			ImageID     string `json:"image_id"`
			Source      string `json:"expected_source_revision"`
			Expectation string `json:"expectation_sha256"`
			Assets      int    `json:"asset_files"`
			Admission   bool   `json:"runtime_admission"`
		} `json:"installed_container_files"`
	}
	if json.Unmarshal(got.Receipt, &receipt) != nil || len(receipt.Installed) != 1 {
		t.Fatal("host installed join receipt lost exact installed evidence")
	}
	joined := receipt.Installed[0]
	if joined.ID != id || joined.ImageID != expected.ImageID || joined.Source != source || joined.Expectation != hostDigest(encoded) || joined.Assets != 34 || joined.Admission {
		t.Fatal("host installed join retained wrong installed identity")
	}
	for _, drift := range []string{"source", "image", "service", "missing container", "unscoped container", "binary", "CA", "asset", "omitted asset", "extra asset"} {
		t.Run(drift, func(t *testing.T) {
			x := r
			x.Installed = append([]HostInstalledRequest{}, r.Installed...)
			x.Installed[0].Expectation.Assets = make(map[string]string, len(expected.Assets))
			for name, hash := range expected.Assets {
				x.Installed[0].Expectation.Assets[name] = hash
			}
			item := &x.Installed[0]
			switch drift {
			case "source":
				item.Expectation.SourceRevision = strings.Repeat("a", 40)
			case "image":
				item.Expectation.ImageID = "sha256:" + strings.Repeat("a", 64)
			case "service":
				item.Service = "exporter"
			case "missing container":
				item.ContainerID = strings.Repeat("a", 64)
			case "unscoped container":
				item.ContainerID = os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_UNSCOPED_CONTAINER")
			case "binary":
				item.Expectation.BinarySHA256 = strings.Repeat("a", 64)
			case "CA":
				item.Expectation.CASHA256 = strings.Repeat("a", 64)
			case "asset":
				item.Expectation.Assets["boards.csv"] = strings.Repeat("a", 64)
			case "omitted asset":
				delete(item.Expectation.Assets, "boards.csv")
			case "extra asset":
				item.Expectation.Assets["fixture-missing.csv"] = strings.Repeat("a", 64)
			}
			call(t, x, false)
		})
	}
	retained, err := os.ReadFile(filepath.Join(state, "preflight-"+got.ReceiptSHA256+".json"))
	if err != nil || !bytes.Equal(retained, got.Receipt) {
		t.Fatal("host installed join refusals changed earlier retained evidence")
	}
	t.Log("actual installed native host preflight joined independently retained binary/system-CA/34 assets to three requested immutable native image generations and exact scoped service/container/source; ten substitutions refused before intent/archive retention; disposable loopback registry/synthetic selected roles; runtime admission false")
}
