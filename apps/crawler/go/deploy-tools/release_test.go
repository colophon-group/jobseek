package main

import (
	"archive/tar"
	"bytes"
	"crypto/sha256"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReleaseDurabilityAndAtomicPointer(t *testing.T) {
	parent := t.TempDir()
	generation := filepath.Join(parent, "releases", "new")
	if err := os.MkdirAll(filepath.Join(generation, "data"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(generation, "data", "boards.csv"), []byte("slug\nfixture\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := syncRelease(generation); err != nil {
		t.Fatal(err)
	}
	active := filepath.Join(parent, "active")
	if err := os.Symlink("old", active); err != nil {
		t.Fatal(err)
	}
	if err := activateRelease(generation, active, parent); err != nil {
		t.Fatal(err)
	}
	if selected, err := os.Readlink(active); err != nil || selected != generation {
		t.Fatal("selected generation differs", selected, err)
	}
	if err := activateRelease(generation, active, t.TempDir()); err == nil {
		t.Fatal("mismatched pointer parent accepted")
	}
	if err := os.Symlink(filepath.Join(generation, "data", "boards.csv"), filepath.Join(generation, "alias")); err != nil {
		t.Fatal(err)
	}
	if err := syncRelease(generation); err == nil {
		t.Fatal("symlink release tree accepted")
	}
	if err := activateRelease(active, filepath.Join(parent, "other"), parent); err == nil {
		t.Fatal("symlink generation accepted")
	}
}

func TestSpecsUSTARRoundTripPreservesOriginalModesAndAbsence(t *testing.T) {
	snapshot := t.TempDir()
	archive := filepath.Join(t.TempDir(), "rollback.tar")
	name := "scripts/executable.sh"
	content := []byte("original snapshot bytes\n")
	if err := os.MkdirAll(filepath.Dir(filepath.Join(snapshot, name)), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(snapshot, name), content, 0755); err != nil {
		t.Fatal(err)
	}
	presenceName := ".presence"
	presence := []byte(fmt.Sprintf("DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1\nPRESENT %x 755 %s\nABSENT - - missing\n", sha256.Sum256(content), name))
	if err := os.WriteFile(filepath.Join(snapshot, presenceName), presence, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(archive, nil, 0600); err != nil {
		t.Fatal(err)
	}
	if err := packSpecs(snapshot, archive, []string{presenceName, name}); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(archive)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	reader := tar.NewReader(f)
	for _, expected := range []string{presenceName, name} {
		h, err := reader.Next()
		if err != nil {
			t.Fatal(err)
		}
		if h.Name != expected || h.Format != tar.FormatUSTAR || h.Uid != 0 || h.Gid != 0 || h.Uname != "" || h.Gname != "" || h.ModTime.Unix() != 0 {
			t.Fatal("original USTAR header contract differs", h)
		}
	}
	if _, err := reader.Next(); err != io.EOF {
		t.Fatal("unexpected archive member", err)
	}
	destination := t.TempDir()
	if err := unpackSpecs(archive, destination, presenceName, []string{name, "missing"}); err != nil {
		t.Fatal(err)
	}
	actual, err := os.ReadFile(filepath.Join(destination, name))
	if err != nil || !bytes.Equal(actual, content) {
		t.Fatal("snapshot bytes differ", err)
	}
	info, err := os.Stat(filepath.Join(destination, name))
	if err != nil || info.Mode().Perm() != 0755 {
		t.Fatal("snapshot executable mode differs", err)
	}
	if _, err := os.Lstat(filepath.Join(destination, "missing")); !os.IsNotExist(err) {
		t.Fatal("absent spec created", err)
	}
	if err := unpackSpecs(archive, destination, presenceName, []string{name, "missing"}); err == nil {
		t.Fatal("existing output overwritten")
	}
	if err := packSpecs(snapshot, archive, []string{"../escape"}); err == nil {
		t.Fatal("traversal accepted")
	}
}

func TestRollbackArchiveRejectsAllMalformedEvidenceBeforeExtraction(t *testing.T) {
	content := []byte("fixture")
	good := fmt.Sprintf("DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1\nPRESENT %x 644 spec\n", sha256.Sum256(content))
	for _, kind := range []string{"digest", "mode", "order", "absence", "format", "newline", "duplicate", "extra", "symlink", "hardlink", "traversal", "invalid-utf8"} {
		t.Run(kind, func(t *testing.T) {
			presence := good
			h := &tar.Header{Name: "spec", Mode: 0644, Size: int64(len(content)), Typeflag: tar.TypeReg}
			switch kind {
			case "digest":
				presence = strings.Replace(good, fmt.Sprintf("%x", sha256.Sum256(content)), strings.Repeat("0", 64), 1)
			case "mode":
				h.Mode = 0755
			case "order":
				presence = strings.Replace(good, " spec\n", " other\n", 1)
			case "absence":
				presence = "DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1\nABSENT - - spec\n"
			case "format":
				presence = strings.Replace(good, "VERSION=1", "VERSION=2", 1)
			case "newline":
				presence = strings.TrimSuffix(good, "\n")
			case "invalid-utf8":
				presence = good + "\xff"
			case "symlink":
				h.Typeflag = tar.TypeSymlink
				h.Linkname = "elsewhere"
				h.Size = 0
			case "hardlink":
				h.Typeflag = tar.TypeLink
				h.Linkname = "elsewhere"
				h.Size = 0
			case "traversal":
				h.Name = "../spec"
			}
			var body bytes.Buffer
			w := tar.NewWriter(&body)
			if err := w.WriteHeader(&tar.Header{Name: ".presence", Mode: 0600, Size: int64(len(presence))}); err != nil {
				t.Fatal(err)
			}
			if _, err := w.Write([]byte(presence)); err != nil {
				t.Fatal(err)
			}
			if err := w.WriteHeader(h); err != nil {
				t.Fatal(err)
			}
			if h.Size > 0 {
				if _, err := w.Write(content); err != nil {
					t.Fatal(err)
				}
			}
			if kind == "duplicate" || kind == "extra" {
				name := "spec"
				if kind == "extra" {
					name = "extra"
				}
				if err := w.WriteHeader(&tar.Header{Name: name, Mode: 0600}); err != nil {
					t.Fatal(err)
				}
			}
			if err := w.Close(); err != nil {
				t.Fatal(err)
			}
			archive := filepath.Join(t.TempDir(), "rollback.tar")
			if err := os.WriteFile(archive, body.Bytes(), 0600); err != nil {
				t.Fatal(err)
			}
			destination := t.TempDir()
			if err := unpackSpecs(archive, destination, ".presence", []string{"spec"}); err == nil {
				t.Fatal("malformed rollback evidence accepted")
			}
			entries, err := os.ReadDir(destination)
			if err != nil || len(entries) != 0 {
				t.Fatal("malformed evidence wrote outputs", err)
			}
		})
	}
}
