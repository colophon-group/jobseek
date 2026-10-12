package main

import (
	"errors"
	"os"
	"path/filepath"
)

// Publish the shell-generated journal only after its bytes reach stable storage.
// The enclosing original publication wrapper owns the global mutation lock.
func publishJournal(temporary, journal, parent string) error {
	if !filepath.IsAbs(parent) || filepath.Clean(parent) != parent || filepath.Dir(temporary) != parent || filepath.Dir(journal) != parent || temporary == journal {
		return errors.New("publication journal parent differs")
	}
	if err := syncDirectory(parent); err != nil {
		return err
	}
	f, err := regularOpen(temporary)
	if err != nil {
		return err
	}
	info, err := f.Stat()
	if err != nil || info.Mode().Perm() != 0600 || info.Size() > 65536 {
		f.Close()
		return errors.New("publication journal is unsafe")
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(temporary, journal); err != nil {
		return err
	}
	return syncDirectory(parent)
}
