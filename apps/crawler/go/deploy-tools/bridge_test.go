package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func bridgeFixture(t *testing.T, format, transitive string) string {
	t.Helper()
	root := t.TempDir()
	revision, contract := strings.Repeat("a", 40), strings.Repeat("b", 64)
	images := func(name string) string {
		return "ghcr.io/colophon-group/" + name + "@sha256:" + strings.Repeat("c", 64)
	}
	environment := "CRAWLER_IMAGE_TAG=retained\nCRAWLER_IMAGE_REF=" + images("jobseek-crawler") + "\nBROWSER_IMAGE_REF=" + images("jobseek-crawler-browser") + "\nSHIM_IMAGE_REF=" + images("jobseek-murmur-shim") + "\nJOBSEEK_DEPLOY_REVISION=" + revision + "\nUNRELATED_VALUE=retained-value\n"
	compose := []byte("services:\n  worker: {}\n")
	attestation := []byte("RUNTIME_ATTESTATION_FORMAT_VERSION=1\nPREVIOUS_REVISION=" + revision + "\nRUNTIME_CONTRACT_SHA256=" + contract + "\nCOMPATIBLE_REVISION=" + revision + "\n")
	files := map[string][]byte{"legacy-source-compose.yml": compose, "legacy-source-environment.env": []byte(environment), "legacy-source-success.env": []byte(environment), "runtime-attestation.env": attestation, "docker-compose.yml": compose, "environment.env": []byte(environment + "JOBSEEK_RUNTIME_CONTRACT_SHA256=" + contract + "\n"), "success.env": []byte(environment + "JOBSEEK_RUNTIME_CONTRACT_SHA256=" + contract + "\n")}
	manifest := "RELEASE_FORMAT_VERSION=3\nDATA_REVISION=" + revision + "\nCOMPOSE_SHA256=" + bridgeHash(compose) + "\nLEGACY_BRIDGE_FORMAT_VERSION=1\nLEGACY_BRIDGE_TRANSITIVE=" + transitive + "\nLEGACY_RUNTIME_ATTESTATION_SHA256=" + bridgeHash(attestation) + "\nLEGACY_SOURCE_RELEASE_FORMAT=" + format + "\nLEGACY_SOURCE_REVISION=" + revision + "\nLEGACY_SOURCE_CRAWLER_IMAGE_REF=" + images("jobseek-crawler") + "\nLEGACY_SOURCE_COMPOSE_SHA256=" + bridgeHash(compose) + "\nLEGACY_SOURCE_ENVIRONMENT_SHA256=" + bridgeHash([]byte(environment)) + "\nLEGACY_SOURCE_SUCCESS_SHA256=" + bridgeHash([]byte(environment)) + "\n"
	if format == "2" {
		b := []byte("services:\n  worker:\n    image: immutable\n")
		files["legacy-source-images.override.yml"] = b
		manifest += "HAS_IMAGE_OVERRIDE=1\nIMAGE_OVERRIDE_SHA256=" + bridgeHash(b) + "\nLEGACY_SOURCE_IMAGE_OVERRIDE_SHA256=" + bridgeHash(b) + "\n"
	} else {
		manifest += "HAS_IMAGE_OVERRIDE=0\n"
	}
	files["release.manifest"] = []byte(manifest)
	for name, body := range files {
		if err := os.WriteFile(filepath.Join(root, name), body, 0600); err != nil {
			t.Fatal(err)
		}
	}
	return root
}
func changeBridgeFile(t *testing.T, root, name, old, new string) {
	t.Helper()
	p := filepath.Join(root, name)
	b, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	if !strings.Contains(string(b), old) {
		t.Fatal("fixture replacement absent")
	}
	if e = os.WriteFile(p, []byte(strings.Replace(string(b), old, new, 1)), 0600); e != nil {
		t.Fatal(e)
	}
}
func rehashBridgeFile(t *testing.T, root, name, key string) {
	t.Helper()
	b, e := os.ReadFile(filepath.Join(root, name))
	if e != nil {
		t.Fatal(e)
	}
	p := filepath.Join(root, "release.manifest")
	m, e := os.ReadFile(p)
	if e != nil {
		t.Fatal(e)
	}
	lines, _ := bridgeLines(m)
	old, e := bridgeValue(lines, key)
	if e != nil {
		t.Fatal(e)
	}
	changeBridgeFile(t, root, "release.manifest", key+"="+old, key+"="+bridgeHash(b))
}

func TestReleaseBridgeMatchesOriginalPythonVerifier(t *testing.T) {
	python, e := exec.LookPath("python3")
	if e != nil {
		t.Fatal("original Python bridge oracle required")
	}
	oracle, e := filepath.Abs("../../../../scripts/verify-crawler-release-bridge.py")
	if e != nil {
		t.Fatal(e)
	}
	cases := []string{"generic", "bridge1", "bridge2", "transitive1", "transitive2", "duplicate-format", "unexpected-legacy", "generic-residue", "generic-dangling-symlink", "invalid-UTF8", "attestation-duplicate", "attestation-foreign", "bad-source-hash", "source-has-runtime", "source-pair-disagrees", "current-pair-disagrees", "initial-environment-changed", "initial-compose-changed", "format1-override-residue", "format2-override-changed", "missing-current-override-hash", "source-file-symlink", "generation-symlink", "invalid-owner"}
	for _, name := range cases {
		t.Run(name, func(t *testing.T) {
			format, transitive := "1", "0"
			if name == "bridge2" || name == "transitive2" || name == "format2-override-changed" {
				format = "2"
			}
			if strings.HasPrefix(name, "transitive") {
				transitive = "1"
			}
			root := bridgeFixture(t, format, transitive)
			owner := "colophon-group"
			switch name {
			case "generic", "generic-residue", "generic-dangling-symlink":
				if e := os.WriteFile(filepath.Join(root, "release.manifest"), []byte("RELEASE_FORMAT_VERSION=3\n"), 0600); e != nil {
					t.Fatal(e)
				}
				for _, f := range []string{"runtime-attestation.env", "legacy-source-compose.yml", "legacy-source-environment.env", "legacy-source-success.env", "legacy-source-images.override.yml"} {
					if e := os.Remove(filepath.Join(root, f)); e != nil && !os.IsNotExist(e) {
						t.Fatal(e)
					}
				}
				if name == "generic-residue" {
					if e := os.WriteFile(filepath.Join(root, "runtime-attestation.env"), nil, 0600); e != nil {
						t.Fatal(e)
					}
				}
				if name == "generic-dangling-symlink" {
					if e := os.Symlink("absent", filepath.Join(root, "runtime-attestation.env")); e != nil {
						t.Fatal(e)
					}
				}
			case "duplicate-format":
				changeBridgeFile(t, root, "release.manifest", "LEGACY_BRIDGE_FORMAT_VERSION=1", "LEGACY_BRIDGE_FORMAT_VERSION=1\nLEGACY_BRIDGE_FORMAT_VERSION=1")
			case "unexpected-legacy":
				changeBridgeFile(t, root, "release.manifest", "LEGACY_BRIDGE_TRANSITIVE=0", "LEGACY_BRIDGE_TRANSITIVE=0\nLEGACY_EXTRA=1")
			case "invalid-UTF8":
				if e := os.WriteFile(filepath.Join(root, "release.manifest"), []byte{255}, 0600); e != nil {
					t.Fatal(e)
				}
			case "attestation-duplicate":
				changeBridgeFile(t, root, "runtime-attestation.env", "COMPATIBLE_REVISION="+strings.Repeat("a", 40), "COMPATIBLE_REVISION="+strings.Repeat("a", 40)+"\nCOMPATIBLE_REVISION="+strings.Repeat("a", 40))
				rehashBridgeFile(t, root, "runtime-attestation.env", "LEGACY_RUNTIME_ATTESTATION_SHA256")
			case "attestation-foreign":
				changeBridgeFile(t, root, "runtime-attestation.env", "COMPATIBLE_REVISION="+strings.Repeat("a", 40), "COMPATIBLE_REVISION="+strings.Repeat("d", 40))
				rehashBridgeFile(t, root, "runtime-attestation.env", "LEGACY_RUNTIME_ATTESTATION_SHA256")
			case "bad-source-hash":
				changeBridgeFile(t, root, "legacy-source-compose.yml", "worker", "different")
			case "source-has-runtime":
				changeBridgeFile(t, root, "legacy-source-environment.env", "UNRELATED_VALUE=retained-value", "JOBSEEK_RUNTIME_CONTRACT_SHA256="+strings.Repeat("b", 64))
				rehashBridgeFile(t, root, "legacy-source-environment.env", "LEGACY_SOURCE_ENVIRONMENT_SHA256")
			case "source-pair-disagrees":
				changeBridgeFile(t, root, "legacy-source-success.env", "CRAWLER_IMAGE_TAG=retained", "CRAWLER_IMAGE_TAG=changed")
				rehashBridgeFile(t, root, "legacy-source-success.env", "LEGACY_SOURCE_SUCCESS_SHA256")
			case "current-pair-disagrees":
				changeBridgeFile(t, root, "success.env", "CRAWLER_IMAGE_TAG=retained", "CRAWLER_IMAGE_TAG=changed")
			case "initial-environment-changed":
				changeBridgeFile(t, root, "environment.env", "UNRELATED_VALUE=retained-value", "UNRELATED_VALUE=changed")
			case "initial-compose-changed":
				changeBridgeFile(t, root, "docker-compose.yml", "worker", "different")
			case "format1-override-residue":
				if e := os.WriteFile(filepath.Join(root, "legacy-source-images.override.yml"), nil, 0600); e != nil {
					t.Fatal(e)
				}
			case "format2-override-changed":
				changeBridgeFile(t, root, "legacy-source-images.override.yml", "immutable", "changed")
			case "missing-current-override-hash":
				changeBridgeFile(t, root, "release.manifest", "HAS_IMAGE_OVERRIDE=0", "HAS_IMAGE_OVERRIDE=1")
			case "source-file-symlink":
				p := filepath.Join(root, "legacy-source-compose.yml")
				if e := os.Remove(p); e != nil {
					t.Fatal(e)
				}
				if e := os.Symlink("docker-compose.yml", p); e != nil {
					t.Fatal(e)
				}
			case "generation-symlink":
				p := filepath.Join(t.TempDir(), "generation")
				if e := os.Symlink(root, p); e != nil {
					t.Fatal(e)
				}
				root = p
			case "invalid-owner":
				owner = "INVALID"
			}
			state, goErr := verifyReleaseBridge(root, owner)
			cmd := exec.Command(python, oracle, "--generation", root, "--owner", owner)
			cmd.Env = []string{"PATH=" + os.Getenv("PATH"), "PYTHONDONTWRITEBYTECODE=1"}
			out, pyErr := cmd.Output()
			if (goErr != nil) != (pyErr != nil) || goErr == nil && strings.TrimSpace(string(out)) != state {
				t.Fatal("native bridge differs from original decision", name, goErr, pyErr)
			}
		})
	}
}

func TestRuntimeAttestationEpochAndLineSemantics(t *testing.T) {
	previous, other, contract := strings.Repeat("a", 40), strings.Repeat("c", 40), strings.Repeat("b", 64)
	body := "RUNTIME_ATTESTATION_FORMAT_VERSION=1\nPREVIOUS_REVISION=" + previous + "\nRUNTIME_CONTRACT_SHA256=" + contract + "\nCOMPATIBLE_REVISION=" + previous + "\nCOMPATIBLE_REVISION=" + other + "\n"
	for _, separator := range []string{"\n", "\r\n", "\r", "\v", "\f", "\x1c", "\u0085", "\u2028", "\u2029"} {
		if rows, e := runtimeAttestation([]byte(strings.ReplaceAll(body, "\n", separator)), previous, contract, other); e != nil || len(rows) != 2 {
			t.Fatal("original splitlines epoch semantics changed", e)
		}
	}
	for _, mutated := range []string{body + "\n", strings.Replace(body, "COMPATIBLE_REVISION="+other, "COMPATIBLE_REVISION="+previous, 1), strings.Replace(body, "PREVIOUS_REVISION="+previous, "PREVIOUS_REVISION="+other, 1), strings.Replace(body, "RUNTIME_ATTESTATION_FORMAT_VERSION=1", "RUNTIME_ATTESTATION_FORMAT_VERSION=2", 1), body + "EXTRA=1\n"} {
		if _, e := runtimeAttestation([]byte(mutated), previous, contract, other); e == nil {
			t.Fatal("invalid runtime epoch admitted")
		}
	}
	if _, e := runtimeAttestation([]byte(body), previous, contract, strings.Repeat("d", 40)); e == nil {
		t.Fatal("unattested source admitted")
	}
}
