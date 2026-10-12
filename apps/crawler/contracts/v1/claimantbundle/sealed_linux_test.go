package claimantbundle

import (
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"testing"
)

// The image build runs this with root privileges. Both retries execute as the
// original unprivileged installer, against an actual sealed claimant-owned key.
func TestPrepareSealedClaimantKeyAndForeignOwnerRetry(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("actual sealed-owner retry requires image-build root")
	}
	payloads := material(t, nil)
	archive, sha := transport(t, payloads, nil)
	parent := t.TempDir()
	root := filepath.Join(parent, "credentials")
	for _, directory := range []string{filepath.Dir(parent), parent, filepath.Dir(archive)} {
		if err := os.Chmod(directory, 0755); err != nil {
			t.Fatal(err)
		}
	}
	binary := filepath.Join(parent, "native-test")
	program, err := os.ReadFile(os.Args[0])
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(binary, program, 0755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(parent, 1000, 1000); err != nil {
		t.Fatal(err)
	}
	if err := os.Chown(archive, 1000, 1000); err != nil {
		t.Fatal(err)
	}
	generation, err := Prepare(archive, sha, testRevision, root, "10.0.0.5")
	if err != nil {
		t.Fatal(err)
	}
	if err = os.Chown(root, 1000, 1000); err != nil {
		t.Fatal(err)
	}
	if err = os.Chown(generation, 1000, 1000); err != nil {
		t.Fatal(err)
	}
	key := filepath.Join(generation, "client-key.pem")
	if err = os.Chown(key, 10001, 10001); err != nil {
		t.Fatal(err)
	}
	command := func(want string) error {
		cmd := exec.Command(binary, "-test.run=^TestNativeSealedRetryHelper$")
		cmd.Env = append(os.Environ(), "CLAIMANT_FIXTURE_ARCHIVE="+archive, "CLAIMANT_FIXTURE_DIGEST="+sha, "CLAIMANT_FIXTURE_ROOT="+root, "CLAIMANT_FIXTURE_EXPECT="+want)
		cmd.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: 1000, Gid: 1000, NoSetGroups: true}}
		body, err := cmd.CombinedOutput()
		if err != nil {
			t.Log(string(body))
		}
		return err
	}
	if err = command("success"); err != nil {
		t.Fatal("sealed claimant retry refused", err)
	}
	if err = os.Chown(key, 10002, 10002); err != nil {
		t.Fatal(err)
	}
	if err = command("failure"); err != nil {
		t.Fatal("foreign private-key ownership admitted", err)
	}
}

func TestNativeSealedRetryHelper(t *testing.T) {
	archive := os.Getenv("CLAIMANT_FIXTURE_ARCHIVE")
	if archive == "" {
		t.Skip("subprocess fixture only")
	}
	if os.Geteuid() != 1000 {
		t.Fatal("installer identity changed")
	}
	root := os.Getenv("CLAIMANT_FIXTURE_ROOT")
	key := filepath.Join(root, "sha-"+testRevision+"-"+os.Getenv("CLAIMANT_FIXTURE_DIGEST"), "client-key.pem")
	if _, err := os.ReadFile(key); !os.IsPermission(err) {
		t.Fatal("sealed key is readable by installer")
	}
	_, err := Prepare(archive, os.Getenv("CLAIMANT_FIXTURE_DIGEST"), testRevision, root, "10.0.0.5")
	if (err == nil) != (os.Getenv("CLAIMANT_FIXTURE_EXPECT") == "success") {
		t.Fatal("sealed or foreign owner contract changed", err)
	}
}
