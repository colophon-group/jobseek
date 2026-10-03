package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

func TestHostContainmentRequiresExplicitModeSourceRequestAndPreflightIntent(t *testing.T) {
	r := hostRequestFixture()
	env := map[string]string{"ORDINARY_GO_WORKER_MODE": "host-contain", "ORDINARY_HOST_COORDINATOR_SOURCE_REVISION": r.CoordinatorSource, "ORDINARY_HOST_REQUEST_DIRECTORY": "/private/request", "ORDINARY_HOST_REQUEST_SHA256": strings.Repeat("b", 64), "ORDINARY_HOST_PREFLIGHT_INTENT_SHA256": strings.Repeat("c", 64)}
	get := func(k string) string { return env[k] }
	if _, err := ReadHostContainmentConfig(get, r.CoordinatorSource); err != nil {
		t.Fatal("explicit containment refused", err)
	}
	for key, old := range env {
		env[key] = ""
		if _, err := ReadHostContainmentConfig(get, r.CoordinatorSource); err == nil {
			t.Fatal("implicit containment admitted", key)
		}
		env[key] = old
	}
	env["ORDINARY_GO_WORKER_MODE"] = "host-preflight"
	if _, err := ReadHostContainmentConfig(get, r.CoordinatorSource); err == nil {
		t.Fatal("read-only mode enabled mutation")
	}
}

func TestHostContainmentRequiresExactCanonicalPreflightIntentAndProtectedArchiveBeforeObservation(t *testing.T) {
	for _, fault := range []string{"missing intent", "intent hash", "intent source", "unknown intent field", "missing archive", "archive hash", "archive alias", "archive permissions", "invalid archive"} {
		t.Run(fault, func(t *testing.T) {
			state := hostPrivateDirectory(t)
			r := hostRequestFixture()
			r.DeploymentDirectory = hostPrivateDirectory(t)
			if os.WriteFile(filepath.Join(r.DeploymentDirectory, "docker-compose.yml"), []byte("services: {}\n"), 0600) != nil {
				t.Fatal("deployed Compose fixture")
			}
			for n := range r.Releases {
				r.Releases[n].Directory = hostPrivateDirectory(t)
			}
			r.Architecture = runtime.GOARCH
			g, _ := releaseExecutableGeneration(t, r.CoordinatorSource)
			g, err := filepath.EvalSymlinks(g)
			if err != nil {
				t.Fatal("physical release fixture", err)
			}
			files, err := release.VerifyFiles(context.Background(), g, r.Owner)
			if err != nil {
				t.Fatal("release fixture", err)
			}
			specs, err := release.CaptureSpecs(context.Background(), release.SpecCaptureConfig{DeploymentDirectory: r.DeploymentDirectory, GenerationDirectory: g, Owner: r.Owner, FileEvidenceSHA256: files.SHA256()})
			if err != nil {
				t.Fatal("spec fixture", err)
			}
			archivePath := filepath.Join(state, "fixture.tar")
			if release.RetainSpecArchive(context.Background(), archivePath, specs) != nil {
				t.Fatal("archive fixture")
			}
			archive, err := os.ReadFile(archivePath)
			if err != nil {
				t.Fatal("archive read fixture")
			}
			if fault == "invalid archive" {
				archive = []byte("invalid fixture archive")
			}
			body, _ := json.Marshal(r)
			if os.WriteFile(filepath.Join(state, "request.json"), body, 0600) != nil {
				t.Fatal("request fixture")
			}
			intent := hostPreflightIntent{"jobseek.crawler-host-preflight-intent/v1", r.CoordinatorSource, hostDigest(body), hostDigest(archive), strings.Repeat("d", 64), strings.Repeat("e", 64)}
			if fault == "intent source" {
				intent.SourceRevision = strings.Repeat("f", 40)
			}
			ib, _ := json.Marshal(intent)
			if fault == "unknown intent field" {
				ib = []byte(strings.Replace(string(ib), "{", `{"unknown":true,`, 1))
			}
			if fault != "missing intent" && os.WriteFile(filepath.Join(state, "intent.json"), ib, 0600) != nil {
				t.Fatal("intent fixture")
			}
			if fault != "missing archive" && os.WriteFile(filepath.Join(state, "deploy-specs.tar"), archive, 0600) != nil {
				t.Fatal("archive fixture")
			}
			config := HostContainmentConfig{HostPreflightConfig{state, hostDigest(body), r.CoordinatorSource}, hostDigest(ib)}
			switch fault {
			case "intent hash":
				config.intentSHA = strings.Repeat("f", 64)
			case "archive hash":
				os.WriteFile(filepath.Join(state, "deploy-specs.tar"), []byte("changed"), 0600)
			case "archive alias":
				os.Rename(filepath.Join(state, "deploy-specs.tar"), filepath.Join(state, "archive-real"))
				os.Symlink("archive-real", filepath.Join(state, "deploy-specs.tar"))
			case "archive permissions":
				os.Chmod(filepath.Join(state, "deploy-specs.tar"), 0644)
			}
			observed := false
			got, err := runHostContainment(context.Background(), config, filepath.Join(state, "mutation.lock"), func(context.Context, HostPreflightRequest) (*hostObservations, error) {
				observed = true
				return nil, nil
			}, func(context.Context, *release.WriterContainmentPlan, []*release.Images, bool, func() error, func() error) (*release.ColdContainers, error) {
				t.Fatal("unsafe inputs reached mutation")
				return nil, nil
			}, nil)
			if err == nil || got != nil || observed {
				t.Fatal("unsafe preflight/archive reached observation", fault)
			}
		})
	}
}

func TestHostStoreArchiveReadUsesSeparateBoundAndSamePrivateIdentityRules(t *testing.T) {
	state := hostPrivateDirectory(t)
	store, err := openHostStore(state)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	body := bytes.Repeat([]byte{'x'}, (8<<20)+1)
	if os.WriteFile(filepath.Join(state, "deploy-specs.tar"), body, 0600) != nil {
		t.Fatal("large archive fixture")
	}
	if _, err := store.read("deploy-specs.tar", false); err == nil {
		t.Fatal("request bound widened")
	}
	if got, err := store.readLimit("deploy-specs.tar", false, 48<<20); err != nil || !bytes.Equal(got, body) {
		t.Fatal("bounded archive read refused", err)
	}
	if _, err := store.readLimit("deploy-specs.tar", false, (48<<20)+1); err == nil {
		t.Fatal("unbounded archive allowed")
	}
	if os.Chmod(filepath.Join(state, "deploy-specs.tar"), 0644) != nil {
		t.Fatal("mode fixture")
	}
	if _, err := store.readLimit("deploy-specs.tar", false, 48<<20); err == nil {
		t.Fatal("unprotected rollback archive allowed")
	}
}
