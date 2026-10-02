//go:build integration

package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

// The installed-image Actions job already owns this never-started container.
// These commands read it only. Agents never run this fixture locally.
func TestActualInstalledContainerFilesRequireExactRetainedManifest(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_INSTALLED_FILES") != "1" {
		t.Skip("explicit disposable installed-image fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" {
		t.Fatal("installed file observation requires the disposable Linux Actions harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	f, err := os.Open(os.Getenv("JOBSEEK_CRAWLER_RELEASE_IMAGE_FILE_PROOF"))
	if err != nil {
		t.Fatal("installed fixture proof open")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil || len(b) > 1<<20 || uniqueJSON(b) != nil {
		t.Fatal("bounded installed fixture proof")
	}
	var proof struct {
		SourceRevision string            `json:"source_revision"`
		ImageID        string            `json:"image_id"`
		Architecture   string            `json:"architecture"`
		BinarySHA256   string            `json:"binary_sha256"`
		CASHA256       string            `json:"system_ca_sha256"`
		Assets         map[string]string `json:"installed_asset_sha256"`
	}
	if json.Unmarshal(b, &proof) != nil || proof.SourceRevision != os.Getenv("JOBSEEK_ORDINARY_IMAGE_SOURCE_REVISION") || proof.Architecture != runtime.GOARCH {
		t.Fatal("installed fixture source/platform identity")
	}
	spec := InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: proof.SourceRevision, ImageID: proof.ImageID, Architecture: proof.Architecture, Kind: "native-ordinary", BinarySHA256: proof.BinarySHA256, CASHA256: proof.CASHA256, Assets: proof.Assets}
	verifyActualInstalledManifest(t, ctx, spec, os.Getenv("JOBSEEK_CRAWLER_RELEASE_IMAGE_CONTAINER"))
	t.Log("actual image-installed binary/system-CA/complete asset bytes verified against retained expected manifest; independent declared drift refused; no source/process/full-host admission")
}

// This fixture observes the unchanged historical image in a separate owned,
// never-started container. The runtime restoration test remains independent.
func TestActualLegacyInstalledContainerFilesRequirePackageAndEntrypoint(t *testing.T) {
	if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_LEGACY_FILES") != "1" {
		t.Skip("explicit disposable historical legacy image fixture required")
	}
	if runtime.GOOS != "linux" || os.Getenv("GITHUB_ACTIONS") != "true" {
		t.Fatal("legacy installed observation requires the disposable Linux Actions harness")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()
	f, err := os.Open(os.Getenv("JOBSEEK_CRAWLER_RELEASE_LEGACY_FILE_PROOF"))
	if err != nil {
		t.Fatal("legacy installed fixture proof open")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, (1<<20)+1))
	if err != nil || len(b) > 1<<20 || uniqueJSON(b) != nil {
		t.Fatal("bounded legacy installed fixture proof")
	}
	var proof struct {
		SourceRevision   string            `json:"source_revision"`
		ImageID          string            `json:"image_id"`
		Architecture     string            `json:"architecture"`
		BinarySHA256     string            `json:"binary_sha256"`
		CASHA256         string            `json:"system_ca_sha256"`
		EntrypointSHA256 string            `json:"entrypoint_sha256"`
		Assets           map[string]string `json:"installed_asset_sha256"`
		SourceFiles      map[string]string `json:"source_file_sha256"`
		PackageFiles     map[string]string `json:"installed_source_sha256"`
	}
	if json.Unmarshal(b, &proof) != nil || proof.SourceRevision != "b75ccb9456bf29c9477f9747c0c2cc3908ad79bb" || proof.Architecture != runtime.GOARCH {
		t.Fatal("legacy installed fixture historical source/platform identity")
	}
	spec := InstalledExpectationSpec{Version: "jobseek.crawler-installed-expectation/v1", SourceRevision: proof.SourceRevision, ImageID: proof.ImageID, Architecture: proof.Architecture, Kind: "legacy-python", BinarySHA256: proof.BinarySHA256, CASHA256: proof.CASHA256, Assets: proof.Assets, SourceFiles: proof.SourceFiles, PackageFiles: proof.PackageFiles, EntrypointSHA256: proof.EntrypointSHA256}
	verifyActualInstalledManifest(t, ctx, spec, os.Getenv("JOBSEEK_CRAWLER_RELEASE_LEGACY_CONTAINER"))
	t.Log("actual historical image Python binary/system-CA/CLI/complete source and installed package/assets verified against retained expected manifest; independent declared drift refused; no source/process/full-host admission")
}

func verifyActualInstalledManifest(t *testing.T, ctx context.Context, spec InstalledExpectationSpec, id string) {
	t.Helper()
	if _, present := spec.Assets["boards.csv"]; !present || len(spec.Assets) < 2 {
		t.Fatal("complete installed fixture requires the root registry and other assets")
	}
	decode := func(s InstalledExpectationSpec) *InstalledExpectation {
		t.Helper()
		body, err := json.Marshal(s)
		if err != nil {
			t.Fatal("installed manifest encoding")
		}
		expected, err := DecodeInstalledExpectation(body, digest(body))
		if err != nil {
			t.Fatal("installed retained manifest", err)
		}
		return expected
	}
	inventory, err := ObserveContainers(ctx)
	if err != nil {
		t.Fatal(err)
	}
	got, err := ObserveInstalledContainerFiles(ctx, inventory, decode(spec), id)
	if err != nil || got == nil || !strings.Contains(got.Body(), `"runtime_admission":false`) || strings.Contains(got.Body(), "boards.csv") {
		t.Fatal("actual installed image file observation", err)
	}
	drifts := []string{"binary", "CA", "asset", "extra required file", "omitted file"}
	if spec.Kind == "legacy-python" {
		drifts = append(drifts, "source", "installed package", "CLI entrypoint")
	}
	clone := func(files map[string]string) map[string]string {
		copied := make(map[string]string, len(files))
		for name, hash := range files {
			copied[name] = hash
		}
		return copied
	}
	for _, drift := range drifts {
		changed := spec
		changed.Assets = clone(spec.Assets)
		changed.SourceFiles = clone(spec.SourceFiles)
		changed.PackageFiles = clone(spec.PackageFiles)
		switch drift {
		case "binary":
			changed.BinarySHA256 = strings.Repeat("a", 64)
		case "CA":
			changed.CASHA256 = strings.Repeat("a", 64)
		case "CLI entrypoint":
			changed.EntrypointSHA256 = strings.Repeat("a", 64)
		case "source", "installed package":
			files := changed.SourceFiles
			if drift == "installed package" {
				files = changed.PackageFiles
			}
			for name := range files {
				files[name] = strings.Repeat("a", 64)
				break
			}
		case "asset":
			changed.Assets["boards.csv"] = strings.Repeat("a", 64)
		case "extra required file":
			changed.Assets["fixture-required-missing.csv"] = strings.Repeat("a", 64)
		case "omitted file":
			// Keep every parent directory expected so the refusal proves the
			// actual omitted file, independent of archive or map traversal order.
			delete(changed.Assets, "boards.csv")
		}
		want := "installed file content identity"
		if drift == "extra required file" {
			want = "complete installed archive membership and trailer"
		} else if drift == "omitted file" {
			want = "expected installed regular file"
		}
		if _, err := ObserveInstalledContainerFiles(ctx, inventory, decode(changed), id); !errors.Is(err, ErrInvalid) || !strings.Contains(err.Error(), want) || ctx.Err() != nil {
			t.Fatal("actual installed manifest drift admitted", drift, err)
		}
	}
}
