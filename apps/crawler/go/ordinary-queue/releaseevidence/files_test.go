package releaseevidence

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
)

const fixtureOwner = "colophon-group"

var fixtureRevision = strings.Repeat("a", 40)
var fixtureRuntime = strings.Repeat("b", 64)

type generation struct {
	directory string
	fields    map[string]string
}

func write(t *testing.T, g generation, name string, b []byte) {
	t.Helper()
	p := filepath.Join(g.directory, name)
	if err := os.MkdirAll(filepath.Dir(p), 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, b, 0600); err != nil {
		t.Fatal(err)
	}
}
func read(t *testing.T, g generation, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(g.directory, name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}
func fixture(t *testing.T) generation {
	t.Helper()
	g := generation{t.TempDir(), map[string]string{"RELEASE_FORMAT_VERSION": "3", "DATA_REVISION": fixtureRevision, "HAS_IMAGE_OVERRIDE": "0"}}
	if err := os.Chmod(g.directory, 0700); err != nil {
		t.Fatal(err)
	}
	base := "CRAWLER_IMAGE_TAG=0.13.902\nCRAWLER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64) + "\nBROWSER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + strings.Repeat("d", 64) + "\nJOBSEEK_DEPLOY_REVISION=" + fixtureRevision + "\nJOBSEEK_RUNTIME_CONTRACT_SHA256=" + fixtureRuntime + "\n"
	write(t, g, "environment.env", []byte(base+"OPAQUE_OPERATOR_VALUE=fixture-sensitive-value\n"))
	write(t, g, "success.env", []byte(base))
	write(t, g, "docker-compose.yml", []byte("services:\n  worker:\n    image: ${CRAWLER_IMAGE_REF}\n"))
	write(t, g, "data/companies/acme.csv", []byte("slug,name\nacme,Acme\n"))
	write(t, g, "data/occupations.csv", []byte("id,slug\n1,engineer\n"))
	write(t, g, "data-files.sha256", []byte(digest(read(t, g, "data/companies/acme.csv"))+"  companies/acme.csv\n"+digest(read(t, g, "data/occupations.csv"))+"  occupations.csv\n"))
	refresh(t, g)
	return g
}
func refresh(t *testing.T, g generation) {
	t.Helper()
	for file, key := range map[string]string{"docker-compose.yml": "COMPOSE_SHA256", "environment.env": "ENVIRONMENT_SHA256", "success.env": "SUCCESS_SHA256", "data-files.sha256": "DATA_FILES_SHA256"} {
		g.fields[key] = digest(read(t, g, file))
	}
	g.fields["DATA_CONTRACT_SHA256"] = g.fields["DATA_FILES_SHA256"]
	write(t, g, "docker-compose.sha256", []byte(g.fields["COMPOSE_SHA256"]+"\n"))
	write(t, g, "environment.sha256", []byte(g.fields["ENVIRONMENT_SHA256"]+"\n"))
	keys := []string{}
	for k := range g.fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, key := range keys {
		b.WriteString(key + "=" + g.fields[key] + "\n")
	}
	write(t, g, "release.manifest", []byte(b.String()))
}
func bridgeFixture(t *testing.T, sourceFormat string, transitive bool) generation {
	t.Helper()
	g := fixture(t)
	sourceEnv := strings.ReplaceAll(string(read(t, g, "environment.env")), "JOBSEEK_RUNTIME_CONTRACT_SHA256="+fixtureRuntime+"\n", "") + "SHIM_IMAGE_REF=ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + strings.Repeat("e", 64) + "\n"
	sourceSuccess := strings.ReplaceAll(string(read(t, g, "success.env")), "JOBSEEK_RUNTIME_CONTRACT_SHA256="+fixtureRuntime+"\n", "") + "SHIM_IMAGE_REF=ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + strings.Repeat("e", 64) + "\n"
	write(t, g, "legacy-source-compose.yml", read(t, g, "docker-compose.yml"))
	write(t, g, "legacy-source-environment.env", []byte(sourceEnv))
	write(t, g, "legacy-source-success.env", []byte(sourceSuccess))
	write(t, g, "runtime-attestation.env", []byte("RUNTIME_ATTESTATION_FORMAT_VERSION=1\nPREVIOUS_REVISION="+fixtureRevision+"\nRUNTIME_CONTRACT_SHA256="+fixtureRuntime+"\nCOMPATIBLE_REVISION="+fixtureRevision+"\nCOMPATIBLE_REVISION="+strings.Repeat("f", 40)+"\n"))
	g.fields["LEGACY_BRIDGE_FORMAT_VERSION"] = "1"
	g.fields["LEGACY_BRIDGE_TRANSITIVE"] = "0"
	g.fields["LEGACY_SOURCE_RELEASE_FORMAT"] = sourceFormat
	g.fields["LEGACY_SOURCE_REVISION"] = fixtureRevision
	g.fields["LEGACY_SOURCE_CRAWLER_IMAGE_REF"] = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64)
	for file, key := range map[string]string{"legacy-source-compose.yml": "LEGACY_SOURCE_COMPOSE_SHA256", "legacy-source-environment.env": "LEGACY_SOURCE_ENVIRONMENT_SHA256", "legacy-source-success.env": "LEGACY_SOURCE_SUCCESS_SHA256", "runtime-attestation.env": "LEGACY_RUNTIME_ATTESTATION_SHA256"} {
		g.fields[key] = digest(read(t, g, file))
	}
	if sourceFormat == "2" {
		override := []byte("services:\n  worker:\n    image: ghcr.io/colophon-group/jobseek-crawler@sha256:" + strings.Repeat("c", 64) + "\n")
		write(t, g, "legacy-source-images.override.yml", override)
		write(t, g, "rollback-images.override.yml", override)
		g.fields["LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256"] = digest(override)
		g.fields["HAS_IMAGE_OVERRIDE"] = "1"
		g.fields["BOOTSTRAP_LEGACY"] = "0"
		g.fields["IMAGE_OVERRIDE_SHA256"] = digest(override)
	}
	if transitive {
		g.fields["LEGACY_BRIDGE_TRANSITIVE"] = "1"
		sourceEnv = strings.ReplaceAll(sourceEnv, strings.Repeat("e", 64), strings.Repeat("f", 64))
		sourceSuccess = strings.ReplaceAll(sourceSuccess, strings.Repeat("e", 64), strings.Repeat("f", 64))
	}
	write(t, g, "environment.env", []byte(sourceEnv+"JOBSEEK_RUNTIME_CONTRACT_SHA256="+fixtureRuntime+"\n"))
	write(t, g, "success.env", []byte(sourceSuccess+"JOBSEEK_RUNTIME_CONTRACT_SHA256="+fixtureRuntime+"\n"))
	refresh(t, g)
	return g
}
func verify(t *testing.T, g generation) *Files {
	t.Helper()
	f, err := VerifyFiles(context.Background(), g.directory, fixtureOwner)
	if err != nil {
		t.Fatal(err)
	}
	return f
}
func refused(t *testing.T, g generation) {
	t.Helper()
	_, err := VerifyFiles(context.Background(), g.directory, fixtureOwner)
	if !errors.Is(err, ErrInvalid) || strings.Contains(err.Error(), "fixture-sensitive-value") || strings.Contains(err.Error(), g.directory) {
		t.Fatal("invalid or leaking rejection", err)
	}
}
func TestExactFilesystemReleaseAndCredentialFreeIdentity(t *testing.T) {
	for _, kind := range []string{"generic", "initial-1", "initial-2", "transitive-1", "transitive-2"} {
		t.Run(kind, func(t *testing.T) {
			g := fixture(t)
			if kind != "generic" {
				g = bridgeFixture(t, string(kind[len(kind)-1]), strings.HasPrefix(kind, "transitive"))
			}
			f := verify(t, g)
			again := verify(t, g)
			if f.Body() != again.Body() || f.SHA256() != digest([]byte(f.Body())) || strings.Contains(f.Body(), "fixture-sensitive-value") || strings.Contains(f.Body(), g.directory) {
				t.Fatal("file evidence unstable or exposed environment")
			}
			var d document
			if json.Unmarshal([]byte(f.Body()), &d) != nil || d.CSVFiles != 2 || d.DataRevision != fixtureRevision || d.RuntimeContractSHA256 != fixtureRuntime || (kind == "generic" && d.BridgeKind != "generic") || (kind != "generic" && d.BridgeKind != "bridge") {
				t.Fatal("canonical identity differs")
			}
		})
	}
}
func TestFilesystemReleaseRejectsUnattestedAndSemanticDrift(t *testing.T) {
	for _, mutate := range []struct {
		name   string
		change func(*testing.T, generation)
	}{
		{"csv-content", func(t *testing.T, g generation) { write(t, g, "data/occupations.csv", []byte("tampered")) }},
		{"csv-extra", func(t *testing.T, g generation) { write(t, g, "data/extra.csv", []byte("extra")) }},
		{"csv-missing", func(t *testing.T, g generation) { _ = os.Remove(filepath.Join(g.directory, "data/occupations.csv")) }},
		{"csv-order", func(t *testing.T, g generation) {
			b := strings.Split(strings.TrimSpace(string(read(t, g, "data-files.sha256"))), "\n")
			write(t, g, "data-files.sha256", []byte(b[1]+"\n"+b[0]+"\n"))
			refresh(t, g)
		}},
		{"csv-path-traversal", func(t *testing.T, g generation) {
			write(t, g, "data-files.sha256", []byte(strings.Repeat("a", 64)+"  ../escape.csv\n"))
			refresh(t, g)
		}},
		{"csv-nonregular", func(t *testing.T, g generation) {
			_ = os.Symlink("occupations.csv", filepath.Join(g.directory, "data/symlink.csv"))
		}},
		{"csv-directory-symlink", func(t *testing.T, g generation) {
			_ = os.Symlink("companies", filepath.Join(g.directory, "data/linked"))
		}},
		{"snapshot-symlink", func(t *testing.T, g generation) {
			_ = os.Remove(filepath.Join(g.directory, "docker-compose.yml"))
			_ = os.Symlink("success.env", filepath.Join(g.directory, "docker-compose.yml"))
		}},
		{"world-readable-env", func(t *testing.T, g generation) { _ = os.Chmod(filepath.Join(g.directory, "environment.env"), 0644) }},
		{"public-generation", func(t *testing.T, g generation) { _ = os.Chmod(g.directory, 0755) }},
		{"runtime-pair", func(t *testing.T, g generation) {
			write(t, g, "success.env", []byte(strings.ReplaceAll(string(read(t, g, "success.env")), fixtureRuntime, strings.Repeat("c", 64))))
			refresh(t, g)
		}},
		{"mutable-image", func(t *testing.T, g generation) {
			for _, name := range []string{"success.env", "environment.env"} {
				write(t, g, name, []byte(strings.ReplaceAll(string(read(t, g, name)), "@sha256:"+strings.Repeat("c", 64), ":latest")))
			}
			refresh(t, g)
		}},
		{"duplicate-identity", func(t *testing.T, g generation) {
			write(t, g, "environment.env", append(read(t, g, "environment.env"), []byte("JOBSEEK_DEPLOY_REVISION="+fixtureRevision+"\n")...))
			refresh(t, g)
		}},
		{"duplicate-manifest", func(t *testing.T, g generation) {
			write(t, g, "release.manifest", append(read(t, g, "release.manifest"), []byte("HAS_IMAGE_OVERRIDE=0\n")...))
		}},
		{"unknown-manifest", func(t *testing.T, g generation) { g.fields["INJECTED_COMMAND"] = "never-execute"; refresh(t, g) }},
		{"bootstrap-flag", func(t *testing.T, g generation) { g.fields["BOOTSTRAP_LEGACY"] = "1"; refresh(t, g) }},
		{"override-residue", func(t *testing.T, g generation) { write(t, g, "rollback-images.override.yml", []byte("unattested")) }},
		{"override-missing", func(t *testing.T, g generation) {
			g.fields["HAS_IMAGE_OVERRIDE"] = "1"
			g.fields["IMAGE_OVERRIDE_SHA256"] = strings.Repeat("a", 64)
			refresh(t, g)
		}},
		{"generic-bridge-residue", func(t *testing.T, g generation) { write(t, g, "runtime-attestation.env", []byte("unattested")) }},
		{"generic-bridge-fields", func(t *testing.T, g generation) { g.fields["LEGACY_BRIDGE_TRANSITIVE"] = "1"; refresh(t, g) }},
		{"extra-generation-file", func(t *testing.T, g generation) { write(t, g, "unattested", []byte("extra")) }},
		{"data-contract-drift", func(t *testing.T, g generation) {
			refresh(t, g)
			write(t, g, "release.manifest", []byte(strings.Replace(string(read(t, g, "release.manifest")), "DATA_CONTRACT_SHA256="+g.fields["DATA_CONTRACT_SHA256"], "DATA_CONTRACT_SHA256="+strings.Repeat("a", 64), 1)))
		}},
	} {
		t.Run(mutate.name, func(t *testing.T) { g := fixture(t); verify(t, g); mutate.change(t, g); refused(t, g) })
	}
}
func TestBridgeParityWithExistingPythonVerifier(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		if os.Getenv("JOBSEEK_CRAWLER_RELEASE_REQUIRE_PYTHON") == "1" {
			t.Fatal("required existing bridge oracle unavailable")
		}
		t.Skip("offline Python bridge oracle unavailable")
	}
	oracle, err := filepath.Abs("../../../../../scripts/verify-crawler-release-bridge.py")
	if err != nil {
		t.Fatal(err)
	}
	run := func(g generation, accepted bool) {
		t.Helper()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		b, err := exec.CommandContext(ctx, python, oracle, "--generation", g.directory, "--owner", fixtureOwner).CombinedOutput()
		if accepted {
			if err != nil || strings.TrimSpace(string(b)) != "bridge" {
				t.Fatal("existing bridge oracle refused native-verified fixture")
			}
		} else if err == nil {
			t.Fatal("existing bridge oracle admitted drift")
		}
		if strings.Contains(string(b), "fixture-sensitive-value") {
			t.Fatal("oracle leaked input")
		}
	}
	for _, format := range []string{"1", "2"} {
		for _, transitive := range []bool{false, true} {
			g := bridgeFixture(t, format, transitive)
			verify(t, g)
			run(g, true)
		}
	}
	for _, mutate := range []struct {
		name   string
		change func(*testing.T, generation)
	}{
		{"source-hash", func(t *testing.T, g generation) { write(t, g, "legacy-source-success.env", []byte("different")) }},
		{"runtime-epoch", func(t *testing.T, g generation) {
			g.fields["LEGACY_SOURCE_REVISION"] = strings.Repeat("0", 40)
			refresh(t, g)
		}},
		{"initial-env-not-exact", func(t *testing.T, g generation) {
			write(t, g, "environment.env", append(read(t, g, "environment.env"), []byte("EXTRA=unattested\n")...))
			refresh(t, g)
		}},
		{"initial-override-changed", func(t *testing.T, g generation) {
			g.fields["IMAGE_OVERRIDE_SHA256"] = digest([]byte("different"))
			write(t, g, "rollback-images.override.yml", []byte("different"))
			refresh(t, g)
		}},
		{"attestation-repeated-epoch", func(t *testing.T, g generation) {
			b := append(read(t, g, "runtime-attestation.env"), []byte("COMPATIBLE_REVISION="+fixtureRevision+"\n")...)
			write(t, g, "runtime-attestation.env", b)
			g.fields["LEGACY_RUNTIME_ATTESTATION_SHA256"] = digest(b)
			refresh(t, g)
		}},
	} {
		t.Run(mutate.name, func(t *testing.T) {
			g := bridgeFixture(t, "2", false)
			mutate.change(t, g)
			refused(t, g)
			run(g, false)
		})
	}
}

func TestFilesystemReleaseRejectsCancelledAndLinkedGeneration(t *testing.T) {
	g := fixture(t)
	verify(t, g)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := VerifyFiles(ctx, g.directory, fixtureOwner); !errors.Is(err, ErrInvalid) {
		t.Fatal("cancelled file verification admitted")
	}
	link := filepath.Join(t.TempDir(), "linked")
	if err := os.Symlink(g.directory, link); err != nil {
		t.Fatal(err)
	}
	if _, err := VerifyFiles(context.Background(), link, fixtureOwner); !errors.Is(err, ErrInvalid) {
		t.Fatal("symlinked generation admitted")
	}
	huge, err := os.OpenFile(filepath.Join(g.directory, "data/occupations.csv"), os.O_RDWR, 0600)
	if err != nil {
		t.Fatal(err)
	}
	if err := huge.Truncate((64 << 20) + 1); err != nil {
		t.Fatal(err)
	}
	_ = huge.Close()
	refused(t, g)
}
