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
	id := os.Getenv("JOBSEEK_CRAWLER_RELEASE_IMAGE_CONTAINER")
	inventory, err := ObserveContainers(ctx)
	if err != nil {
		t.Fatal(err)
	}
	got, err := ObserveInstalledContainerFiles(ctx, inventory, decode(spec), id)
	if err != nil || got == nil || !strings.Contains(got.Body(), `"runtime_admission":false`) || strings.Contains(got.Body(), "boards.csv") {
		t.Fatal("actual installed image file observation", err)
	}
	for _, drift := range []string{"binary", "CA", "asset", "extra required file", "omitted file"} {
		changed := spec
		changed.Assets = map[string]string{}
		for name, hash := range spec.Assets {
			changed.Assets[name] = hash
		}
		switch drift {
		case "binary":
			changed.BinarySHA256 = strings.Repeat("a", 64)
		case "CA":
			changed.CASHA256 = strings.Repeat("a", 64)
		case "asset":
			for name := range changed.Assets {
				changed.Assets[name] = strings.Repeat("a", 64)
				break
			}
		case "extra required file":
			changed.Assets["fixture-required-missing.csv"] = strings.Repeat("a", 64)
		case "omitted file":
			if len(changed.Assets) < 2 {
				t.Fatal("complete asset fixture requires at least two files")
			}
			for name := range changed.Assets {
				delete(changed.Assets, name)
				break
			}
		}
		if _, err := ObserveInstalledContainerFiles(ctx, inventory, decode(changed), id); !errors.Is(err, ErrInvalid) {
			t.Fatal("actual installed manifest drift admitted", drift, err)
		}
	}
	t.Log("actual image-installed binary/system-CA/complete asset bytes verified against retained expected manifest; independent declared drift refused; no source/process/full-host admission")
}
