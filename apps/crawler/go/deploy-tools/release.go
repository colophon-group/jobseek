package main

import (
	"crypto/rand"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

func regularOpen(path string) (*os.File, error) {
	return regularOpenFlags(path, os.O_RDONLY)
}

func regularOpenFlags(path string, flags int) (*os.File, error) {
	before, err := os.Lstat(path)
	if err != nil || !before.Mode().IsRegular() {
		return nil, errors.New("deployment input is unavailable or unsafe")
	}
	f, err := os.OpenFile(path, flags, 0)
	if err != nil {
		return nil, err
	}
	after, err := f.Stat()
	if err != nil || !after.Mode().IsRegular() || !os.SameFile(before, after) {
		f.Close()
		return nil, errors.New("deployment input changed during open")
	}
	return f, nil
}

func syncDirectory(path string) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("deployment directory is unavailable or unsafe")
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(info, opened) {
		return errors.New("deployment directory changed")
	}
	return f.Sync()
}

func activateRelease(generation, active, parent string) error {
	parent, err := filepath.Abs(parent)
	if err != nil {
		return err
	}
	active, err = filepath.Abs(active)
	if err != nil || filepath.Dir(active) != parent {
		return errors.New("release pointer parent differs")
	}
	generation, err = filepath.Abs(generation)
	if err != nil {
		return err
	}
	info, err := os.Lstat(generation)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("release generation is unsafe")
	}
	if err := syncDirectory(parent); err != nil {
		return err
	}
	var token [16]byte
	if _, err := rand.Read(token[:]); err != nil {
		return err
	}
	temporary := fmt.Sprintf("%s.%x", active, token)
	if err := os.Symlink(generation, temporary); err != nil {
		return err
	}
	defer os.Remove(temporary)
	if err := os.Rename(temporary, active); err != nil {
		return err
	}
	return syncDirectory(parent)
}

func syncRelease(generation string) error {
	info, err := os.Lstat(generation)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("release generation is unsafe")
	}
	var directories []string
	err = filepath.WalkDir(generation, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("release contains a symlink")
		}
		if entry.IsDir() {
			directories = append(directories, path)
			return nil
		}
		f, err := regularOpen(path)
		if err != nil {
			return err
		}
		err = f.Sync()
		closeErr := f.Close()
		if err != nil {
			return err
		}
		return closeErr
	})
	if err != nil {
		return err
	}
	for i := len(directories) - 1; i >= 0; i-- {
		if err := syncDirectory(directories[i]); err != nil {
			return err
		}
	}
	return syncDirectory(filepath.Dir(filepath.Clean(generation)))
}
