package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestJournalDurablePublication(t *testing.T) {
	root := t.TempDir()
	journal := filepath.Join(root, "journal")
	temporary := filepath.Join(root, "journal.pending")
	before := []byte("old recovery state\n")
	after := []byte("PUBLICATION_FORMAT_VERSION=1\nSYNC_SUCCEEDED=1\n")
	if err := os.WriteFile(journal, before, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(temporary, after, 0600); err != nil {
		t.Fatal(err)
	}
	if err := run([]string{"journal-publish", temporary, journal, root}); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(journal)
	if err != nil || string(got) != string(after) {
		t.Fatalf("published bytes differ: %v", err)
	}
	if _, err := os.Lstat(temporary); !os.IsNotExist(err) {
		t.Fatal("temporary remains")
	}
	if err := run([]string{"directory-fsync", root}); err != nil {
		t.Fatal(err)
	}
}

func TestJournalRefusalPreservesRecoveryState(t *testing.T) {
	for _, kind := range []string{"wrong-parent", "same-path", "symlink", "directory", "permissions", "oversized", "missing"} {
		t.Run(kind, func(t *testing.T) {
			root := t.TempDir()
			journal := filepath.Join(root, "journal")
			temporary := filepath.Join(root, "journal.pending")
			before := []byte("retained recovery state\n")
			if err := os.WriteFile(journal, before, 0600); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "wrong-parent":
				temporary = filepath.Join(t.TempDir(), "journal.pending")
				if err := os.WriteFile(temporary, []byte("candidate"), 0600); err != nil {
					t.Fatal(err)
				}
			case "same-path":
				temporary = journal
			case "symlink":
				if err := os.Symlink(journal, temporary); err != nil {
					t.Fatal(err)
				}
			case "directory":
				if err := os.Mkdir(temporary, 0700); err != nil {
					t.Fatal(err)
				}
			case "permissions":
				if err := os.WriteFile(temporary, []byte("candidate"), 0644); err != nil {
					t.Fatal(err)
				}
			case "oversized":
				if err := os.WriteFile(temporary, make([]byte, 65537), 0600); err != nil {
					t.Fatal(err)
				}
			}
			if err := publishJournal(temporary, journal, root); err == nil {
				t.Fatal("unsafe publication accepted")
			}
			got, err := os.ReadFile(journal)
			if err != nil || string(got) != string(before) {
				t.Fatalf("recovery evidence changed: %v", err)
			}
		})
	}
}
