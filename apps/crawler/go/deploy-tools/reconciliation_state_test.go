package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReconciliationStateAtomicPublicationValidationAndRecovery(t *testing.T) {
	root := filepath.Join(t.TempDir(), "state")
	revision, digest := strings.Repeat("a", 40), strings.Repeat("b", 64)
	if err := installReconciliationState(root, revision, digest, -1, -1); err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{"deployed-sha": revision, "wrapper-sha256": digest} {
		info, err := os.Stat(filepath.Join(root, name))
		if err != nil || info.Mode().Perm() != 0640 {
			t.Fatal("state mode", err)
		}
		if !info.Mode().IsRegular() {
			t.Fatal("state is not a regular file")
		}
		raw, _ := os.ReadFile(filepath.Join(root, name))
		if string(raw) != want+"\n" {
			t.Fatal("state format changed")
		}
	}
	info, _ := os.Stat(root)
	if info.Mode().Perm() != 0750 {
		t.Fatal("state directory mode")
	}
	before, _ := os.Stat(filepath.Join(root, "deployed-sha"))
	next := strings.Repeat("c", 40)
	if err := installReconciliationState(root, next, digest, -1, -1); err != nil {
		t.Fatal(err)
	}
	after, _ := os.Stat(filepath.Join(root, "deployed-sha"))
	if os.SameFile(before, after) {
		t.Fatal("state was overwritten in place")
	}
	for _, test := range []struct{ revision, digest string }{{"bad", digest}, {revision, "bad"}, {strings.ToUpper(revision), digest}, {revision, digest + "\n"}} {
		if err := installReconciliationState(root, test.revision, test.digest, -1, -1); err == nil {
			t.Fatal("invalid state accepted")
		}
		raw, _ := os.ReadFile(filepath.Join(root, "deployed-sha"))
		if string(raw) != next+"\n" {
			t.Fatal("invalid pair partially changed prior state")
		}
	}
	if err := os.WriteFile(filepath.Join(root, "deployed-sha"), []byte("corrupt"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := installReconciliationState(root, revision, digest, -1, -1); err != nil {
		t.Fatal("corrupt state not recoverable", err)
	}
	if err := os.Remove(filepath.Join(root, "wrapper-sha256")); err != nil {
		t.Fatal(err)
	}
	if err := installReconciliationState(root, revision, digest, -1, -1); err != nil {
		t.Fatal("missing state not recoverable", err)
	}
}

func TestReconciliationStateRefusesUnsafeFilesAndPublicationFailure(t *testing.T) {
	root := t.TempDir()
	outside := filepath.Join(t.TempDir(), "outside")
	revision := strings.Repeat("a", 40)
	if err := os.WriteFile(outside, []byte(revision+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "deployed-sha")
	if err := os.Symlink(outside, link); err != nil {
		t.Fatal(err)
	}
	if _, err := readReconciliationValue(root, "deployed-sha", reconciliationRevision, "deployment revision"); err == nil {
		t.Fatal("symlink state accepted")
	}
	linkedRoot := filepath.Join(t.TempDir(), "linked-state")
	if err := os.Symlink(root, linkedRoot); err != nil {
		t.Fatal(err)
	}
	if err := installReconciliationState(linkedRoot, revision, strings.Repeat("b", 64), -1, -1); err == nil {
		t.Fatal("symlink state directory accepted")
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(link, 0700); err != nil {
		t.Fatal(err)
	}
	if err := installReconciliationValue(root, "deployed-sha", revision, -1, -1); err == nil {
		t.Fatal("failed atomic rename reported success")
	}
	files, _ := os.ReadDir(root)
	if len(files) != 1 || files[0].Name() != "deployed-sha" {
		t.Fatal("failed publication leaked a temporary state file")
	}
	if err := os.Remove(link); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(link, []byte(strings.Repeat(" ", 5000)+revision), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readReconciliationValue(root, "deployed-sha", reconciliationRevision, "deployment revision"); err == nil {
		t.Fatal("oversized state accepted")
	}
}

func TestReconciliationStateCLIReadbackAndIndependentAttestations(t *testing.T) {
	root := t.TempDir()
	revision, digest := strings.Repeat("a", 40), strings.Repeat("b", 64)
	if err := installReconciliationState(root, revision, digest, -1, -1); err != nil {
		t.Fatal(err)
	}
	old := sourceRevision
	sourceRevision = revision
	defer func() { sourceRevision = old }()
	for _, test := range []struct {
		args   []string
		status int
	}{
		{[]string{"check"}, 0},
		{[]string{"check", "--expected-revision", revision, "--expected-wrapper-sha256", digest}, 0},
		{[]string{"check", "--expected-revision", strings.Repeat("c", 40)}, 1},
		{[]string{"check", "--expected-wrapper-sha256", strings.Repeat("c", 64)}, 1},
		{[]string{"check", "extra"}, 2},
		{[]string{"--help"}, 0},
	} {
		var stdout, stderr bytes.Buffer
		got := runReconciliationState(append([]string{"--state-root", root}, test.args...), &stdout, &stderr)
		if got != test.status {
			t.Fatal(test.args, got, stderr.String())
		}
		if got != 0 && stdout.Len() != 0 {
			t.Fatal("failure emitted successful attestation")
		}
	}
	sourceRevision = strings.Repeat("d", 40)
	var stdout, stderr bytes.Buffer
	if runReconciliationState([]string{"--state-root", root, "check"}, &stdout, &stderr) != 1 || !strings.Contains(stderr.String(), "compiled source") {
		t.Fatal("foreign binary accepted retained state")
	}
}
