package compatibility

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"testing"
)

type baselineManifest struct {
	Format                  string `json:"format"`
	Source                  string `json:"source"`
	DescriptorSHA256        string `json:"descriptor_sha256"`
	IntroductionProtoSHA256 string `json:"introduction_proto_sha256"`
	IntroductionBaseSHA     string `json:"introduction_base_sha"`
	Generator               string `json:"generator"`
}

func contractRoot(t *testing.T) string {
	t.Helper()
	_, filename, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot resolve compatibility test path")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(filename), "..", ".."))
}

func TestPythonCompatibilityGateChecksExactGitBase(t *testing.T) {
	root := contractRoot(t)
	command := exec.Command(
		"python3",
		filepath.Join(root, "tools", "check_compatibility.py"),
		"--root",
		root,
	)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("compatibility gate failed: %v\n%s", err, output)
	}
	if !bytes.Contains(output, []byte("runtime v1 compatibility: ok")) {
		t.Fatalf("compatibility gate did not report success: %s", output)
	}
}

func TestFrozenDescriptorManifestAndGenerationAreDeterministic(t *testing.T) {
	root := contractRoot(t)
	manifestBytes, err := os.ReadFile(filepath.Join(root, "baseline", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var manifest baselineManifest
	if err := json.Unmarshal(manifestBytes, &manifest); err != nil {
		t.Fatal(err)
	}
	if manifest.Format != "google.protobuf.FileDescriptorSet/base64" ||
		manifest.Source != "runtime.proto" ||
		manifest.IntroductionBaseSHA == "" ||
		manifest.Generator == "" {
		t.Fatalf("invalid baseline manifest: %+v", manifest)
	}

	encoded, err := os.ReadFile(
		filepath.Join(root, "baseline", "runtime-v1.descriptor.b64"),
	)
	if err != nil {
		t.Fatal(err)
	}
	descriptor, err := base64.StdEncoding.DecodeString(string(bytes.TrimSpace(encoded)))
	if err != nil {
		t.Fatal(err)
	}
	descriptorDigest := sha256.Sum256(descriptor)
	if hex.EncodeToString(descriptorDigest[:]) != manifest.DescriptorSHA256 {
		t.Fatal("descriptor digest does not match immutable manifest")
	}
	proto, err := os.ReadFile(filepath.Join(root, "runtime.proto"))
	if err != nil {
		t.Fatal(err)
	}
	protoDigest := sha256.Sum256(proto)
	if hex.EncodeToString(protoDigest[:]) != manifest.IntroductionProtoSHA256 {
		t.Fatal("introduction proto digest does not match immutable manifest")
	}

	first := filepath.Join(t.TempDir(), "first.pb")
	second := filepath.Join(t.TempDir(), "second.pb")
	for _, output := range []string{first, second} {
		command := exec.Command(
			"protoc",
			"--proto_path="+root,
			"--include_imports",
			"--descriptor_set_out="+output,
			"runtime.proto",
		)
		command.Dir = root
		if combined, err := command.CombinedOutput(); err != nil {
			t.Fatalf("protoc failed: %v\n%s", err, combined)
		}
	}
	firstBytes, err := os.ReadFile(first)
	if err != nil {
		t.Fatal(err)
	}
	secondBytes, err := os.ReadFile(second)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(firstBytes, secondBytes) || !bytes.Equal(firstBytes, descriptor) {
		t.Fatal("descriptor generation is not deterministic or differs from baseline")
	}
}
