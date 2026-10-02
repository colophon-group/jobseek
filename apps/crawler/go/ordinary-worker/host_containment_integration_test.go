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
	"syscall"
	"testing"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// Setup and cleanup Docker mutations belong to the disposable Actions harness.
// This test invokes only the image-installed coordinator's fixed stop phase;
// it never receives production authority. Sleep processes are writer stand-ins.
func TestActualInstalledNativeHostContainmentStopsAllWritersAndRecoversSIGKILL(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_HOST_CONTAINMENT") != "1" {
		t.Skip("explicit disposable installed containment fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" || os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY") == "" {
		t.Fatal("containment requires disposable Linux installed-image harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 540*time.Second)
	defer cancel()
	source := ordinaryFixtureSourceRevision(t)
	b, err := os.ReadFile(os.Getenv("JOBSEEK_CRAWLER_RELEASE_HOST_JOIN_PROOF"))
	var proof struct {
		Source       string            `json:"source_revision"`
		ImageID      string            `json:"image_id"`
		Architecture string            `json:"architecture"`
		Binary       string            `json:"binary_sha256"`
		CA           string            `json:"system_ca_sha256"`
		Assets       map[string]string `json:"installed_asset_sha256"`
		Reference    string            `json:"immutable_reference"`
	}
	if err != nil || len(b) > 1<<20 || json.Unmarshal(b, &proof) != nil || proof.Source != source || proof.Architecture != runtime.GOARCH || len(proof.Assets) != 34 {
		t.Fatal("independent installed containment identity")
	}
	expect := release.InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: source, ImageID: proof.ImageID, Architecture: runtime.GOARCH, Kind: "native-ordinary", BinarySHA256: proof.Binary, CASHA256: proof.CA, Assets: proof.Assets}
	deployment := hostPrivateDirectory(t)
	if os.WriteFile(filepath.Join(deployment, "docker-compose.yml"), []byte("services: {}\n"), 0600) != nil {
		t.Fatal("private spec fixture")
	}
	r := HostPreflightRequest{Version: "jobseek.crawler-host-preflight-request/v1", CoordinatorSource: source, Owner: "colophon-group", Project: "jobseek-native-host-contain", Architecture: runtime.GOARCH, DeploymentDirectory: deployment, Installed: []HostInstalledRequest{}}
	compose := "services:\n  postgres:\n    image: postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193\n    environment:\n      POSTGRES_USER: crawler\n      POSTGRES_PASSWORD: crawler\n      POSTGRES_DB: jobseek_ordinary_worker_test\n"
	for _, service := range []string{"worker", "exporter"} {
		compose += "  " + service + ":\n    image: " + proof.Reference + "\n    user: '10001:10001'\n    entrypoint: ['/bin/sleep']\n    command: ['600']\n"
	}
	for _, role := range []string{"active", "incoming", "rollback"} {
		g, files := releaseExecutableGeneration(t, source)
		if role == "active" {
			g = hostSelectActiveFixture(t, deployment, g)
		}
		old := hostDigest([]byte(files["docker-compose.yml"]))
		for name, body := range map[string][]byte{"docker-compose.yml": []byte(compose), "docker-compose.sha256": []byte(hostDigest([]byte(compose)) + "\n"), "release.manifest": []byte(strings.ReplaceAll(files["release.manifest"], old, hostDigest([]byte(compose))))} {
			if os.WriteFile(filepath.Join(g, name), body, 0600) != nil {
				t.Fatal("containment generation")
			}
		}
		f, err := release.VerifyFiles(ctx, g, r.Owner)
		if err != nil {
			t.Fatal("containment generation verification", err)
		}
		r.Releases = append(r.Releases, HostReleaseRequest{role, g, f.SHA256()})
	}
	worker, exporter := os.Getenv("JOBSEEK_CRAWLER_RELEASE_CONTAINMENT_WORKER"), os.Getenv("JOBSEEK_CRAWLER_RELEASE_CONTAINMENT_EXPORTER")
	if !hostContainerPattern.MatchString(worker) || !hostContainerPattern.MatchString(exporter) || worker == exporter {
		t.Fatal("explicit owned writer identities")
	}
	r.Installed = []HostInstalledRequest{{"active", "worker", worker, expect}, {"active", "exporter", exporter, expect}}
	writeRequest := func(request HostPreflightRequest) (string, []byte) {
		t.Helper()
		state := hostPrivateDirectory(t)
		b, _ := json.Marshal(request)
		if os.WriteFile(filepath.Join(state, "request.json"), b, 0600) != nil {
			t.Fatal("containment request")
		}
		return state, b
	}
	command := func(state string, body []byte, operation, intent string) *exec.Cmd {
		cmd := exec.CommandContext(ctx, os.Getenv("JOBSEEK_ORDINARY_IMAGE_BINARY"), "--"+operation)
		cmd.Env = []string{"ORDINARY_GO_WORKER_MODE=" + operation, "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION=" + source, "ORDINARY_HOST_REQUEST_DIRECTORY=" + state, "ORDINARY_HOST_REQUEST_SHA256=" + hostDigest(body), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256=" + intent, "LOCAL_DATABASE_URL=invalid", "REDIS_URL=invalid", "DOCKER_HOST=tcp://127.0.0.1:1", "DOCKER_CONFIG=/caller-should-not-load"}
		return cmd
	}
	preflight := func(state string, body []byte) *HostPreflightResult {
		t.Helper()
		out, err := command(state, body, "host-preflight", "").CombinedOutput()
		var result HostPreflightResult
		if err != nil || json.Unmarshal(out, &result) != nil || result.RuntimeAdmission || result.SourceRevision != source || result.ReceiptSHA256 != hostDigest(result.Receipt) {
			hostContainmentFixtureDiagnostic(t, ctx, r)
			t.Fatal("actual containment preflight", err)
		}
		return &result
	}
	// Real installed operation refuses uncovered exporter before intent/effects.
	omitted := r
	omitted.Installed = append([]HostInstalledRequest{}, r.Installed[:1]...)
	omittedState, omittedBody := writeRequest(omitted)
	omittedPreflight := preflight(omittedState, omittedBody)
	out, err := command(omittedState, omittedBody, "host-contain", omittedPreflight.IntentSHA256).CombinedOutput()
	if err == nil || strings.TrimSpace(string(out)) != "ordinary host containment rejected" || ctx.Err() != nil {
		t.Fatal("uncovered exporter admitted", err)
	}
	if _, err := os.Lstat(filepath.Join(omittedState, "containment-intent.json")); !os.IsNotExist(err) {
		t.Fatal("uncovered writer intent retained")
	}
	state, body := writeRequest(r)
	first := preflight(state, body)
	// Kernel SIGKILL after named durable phases, with no runtime fault knobs.
	// Publication may be interrupted before its directory fsync; exact retry
	// must finish that publication rather than adopt different container IDs.
	for _, name := range []string{"containment-intent.json", "restarts-disabled.json"} {
		t.Run("SIGKILL after "+name, func(t *testing.T) {
			cmd := command(state, body, "host-contain", first.IntentSHA256)
			var output bytes.Buffer
			cmd.Stdout, cmd.Stderr = &output, &output
			if cmd.Start() != nil {
				t.Fatal("containment crash child")
			}
			done := make(chan error, 1)
			go func() { done <- cmd.Wait() }()
			deadline := time.NewTimer(150 * time.Second)
			defer deadline.Stop()
			ticker := time.NewTicker(2 * time.Millisecond)
			defer ticker.Stop()
			defer func() { _ = cmd.Process.Kill() }()
			for {
				select {
				case err := <-done:
					t.Fatal("containment exited before crash seam", name, err)
				case <-deadline.C:
					t.Fatal("containment crash seam timeout", name)
				case <-ticker.C:
					if _, err := os.Lstat(filepath.Join(state, name)); err == nil {
						if cmd.Process.Signal(syscall.SIGSTOP) != nil || cmd.Process.Kill() != nil {
							t.Fatal("kernel containment crash")
						}
						if err := <-done; err == nil {
							t.Fatal("SIGKILL child succeeded")
						}
						return
					}
				}
			}
		})
	}
	out, err = command(state, body, "host-contain", first.IntentSHA256).CombinedOutput()
	var result HostContainmentResult
	if err != nil || json.Unmarshal(out, &result) != nil || result.Version != "jobseek.crawler-host-containment-result/v1" || result.SourceRevision != source || result.Phase != "docker_writers_contained" || result.RuntimeAdmission || result.SQLBarriersObserved || result.ReceiptSHA256 != hostDigest(result.Receipt) {
		t.Fatal("actual installed containment/recovery failed", err)
	}
	if bytes.Contains(out, []byte(state)) || bytes.Contains(out, []byte(deployment)) || bytes.Contains(out, []byte("fixture-sensitive-value")) {
		t.Fatal("containment disclosed private inputs")
	}
	retained, err := os.ReadFile(filepath.Join(state, "containment-"+result.ReceiptSHA256+".json"))
	if err != nil || !bytes.Equal(retained, result.Receipt) {
		t.Fatal("completion before durable containment receipt")
	}
	intent, err := os.ReadFile(filepath.Join(state, "containment-intent.json"))
	var bound hostContainmentIntent
	if err != nil || hostDigest(intent) != result.IntentSHA256 || canonicalHostDecode(intent, &bound) != nil || bound.PreflightIntentSHA256 != first.IntentSHA256 {
		t.Fatal("containment lost original intent")
	}
	plan, err := release.DecodeWriterContainmentPlan(bound.Plan, bound.PlanSHA256)
	ids := map[string]bool{}
	if err != nil {
		t.Fatal("actual retained plan", err)
	}
	for _, id := range plan.TargetIDs() {
		ids[id] = true
	}
	if len(ids) != 2 || !ids[worker] || !ids[exporter] {
		t.Fatal("exact all-writer/exporter targets lost")
	}
	inventory, err := release.ObserveContainers(ctx)
	if err != nil {
		t.Fatal("actual post-containment readback", err)
	}
	var observed struct {
		Containers []struct {
			ID            string
			Service       string
			Project       string
			Running       bool
			PID           int64
			RestartPolicy string `json:"restart_policy"`
			Retry         int64  `json:"maximum_retry_count"`
		}
	}
	if json.Unmarshal([]byte(inventory.Body()), &observed) != nil {
		t.Fatal("actual inventory decode")
	}
	infra := 0
	for _, row := range observed.Containers {
		if ids[row.ID] && (row.Running || row.PID != 0 || row.RestartPolicy != "no" || row.Retry != 0) {
			t.Fatal("writer/exporter not contained")
		}
		if row.Project == r.Project && row.Service == "postgres" {
			if !row.Running || row.PID <= 0 {
				t.Fatal("passive Postgres stopped")
			}
			infra++
		}
	}
	if infra != 1 {
		t.Fatal("required passive infrastructure missing")
	}
	out, err = command(state, body, "host-contain", first.IntentSHA256).CombinedOutput()
	var retry HostContainmentResult
	if err != nil || json.Unmarshal(out, &retry) != nil || retry.IntentSHA256 != result.IntentSHA256 || retry.ReceiptSHA256 != result.ReceiptSHA256 {
		t.Fatal("actual exact containment retry changed evidence", err)
	}
	archive := filepath.Join(state, "deploy-specs.tar")
	original, err := os.ReadFile(archive)
	if err != nil {
		t.Fatal("archive fixture")
	}
	if os.WriteFile(archive, []byte("changed rollback bytes"), 0600) != nil {
		t.Fatal("archive drift fixture")
	}
	out, err = command(state, body, "host-contain", first.IntentSHA256).CombinedOutput()
	if err == nil || strings.TrimSpace(string(out)) != "ordinary host containment rejected" || ctx.Err() != nil {
		t.Fatal("containment adopted changed rollback archive", err)
	}
	if os.WriteFile(archive, original, 0600) != nil {
		t.Fatal("archive restore fixture")
	}
	t.Log("actual installed native host containment retained exact writer/exporter IDs and rollback intent before effects; disabled restarts durably before stopping; full daemon readback kept matched Postgres live; uncovered exporter and changed rollback archive refused; two kernel SIGKILL phases and exact retry recovered; sleeping writer stand-ins only; SQL barriers and runtime admission false")
}

// These observer errors contain only library-owned closed phase labels. Never
// print raw inspect/Compose/env bytes or the installed command's arbitrary output.
func hostContainmentFixtureDiagnostic(t *testing.T, ctx context.Context, r HostPreflightRequest) {
	t.Helper()
	selection, err := release.ObserveSelectedActiveFiles(ctx, release.ActiveSelectionConfig{DeploymentDirectory: r.DeploymentDirectory, GenerationDirectory: r.Releases[0].Directory, Owner: r.Owner, FileEvidenceSHA256: r.Releases[0].FileEvidenceSHA256})
	if err != nil || selection == nil {
		t.Log("containment fixture selected observation refused", err)
		return
	}
	images := []*release.Images{}
	seen := map[string]bool{}
	for _, g := range r.Releases {
		f, err := release.VerifyFiles(ctx, g.Directory, r.Owner)
		if err != nil || f.SHA256() != g.FileEvidenceSHA256 {
			t.Log("containment fixture generation refused", g.Role, err)
			return
		}
		i, err := release.ObserveImages(ctx, release.ImageObservationConfig{Directory: g.Directory, Owner: r.Owner, Project: r.Project, FileEvidenceSHA256: g.FileEvidenceSHA256, Architecture: r.Architecture, ProjectDirectory: r.DeploymentDirectory})
		if err != nil {
			t.Log("containment fixture image observation refused", g.Role, err)
			return
		}
		if !seen[i.SHA256()] {
			images = append(images, i)
			seen[i.SHA256()] = true
		}
	}
	inventory, err := release.ObserveContainers(ctx)
	if err != nil {
		t.Log("containment fixture inventory refused", err)
		return
	}
	if _, err := release.RequireContainerExecution(ctx, inventory, images); err != nil {
		t.Log("containment fixture declared execution refused", err)
		return
	}
	for _, item := range r.Installed {
		b, _ := json.Marshal(item.Expectation)
		expected, err := release.DecodeInstalledExpectation(b, hostDigest(b))
		if err != nil {
			t.Log("containment fixture expected files refused", err)
			return
		}
		if _, err := release.ObserveInstalledContainerFiles(ctx, inventory, expected, item.ContainerID); err != nil {
			t.Log("containment fixture installed files refused", item.Service, err)
			return
		}
	}
	_, err = release.CaptureSpecs(ctx, release.SpecCaptureConfig{DeploymentDirectory: r.DeploymentDirectory, GenerationDirectory: r.Releases[0].Directory, Owner: r.Owner, FileEvidenceSHA256: r.Releases[0].FileEvidenceSHA256})
	if err != nil {
		t.Log("containment fixture specs refused", err)
		return
	}
	t.Log("containment fixture independent bounded observers pass; connected installed preflight refusal remains")
}
