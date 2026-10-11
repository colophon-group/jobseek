package main

import (
	"bytes"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
)

var csvName = regexp.MustCompile(`^[A-Za-z0-9._/-]+\.csv$`)

func csvManifest(root string) ([]byte, error) {
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("CSV snapshot root is unavailable or unsafe")
	}
	rows := map[string]string{}
	err = filepath.WalkDir(root, func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return errors.New("CSV snapshot cannot be read completely")
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("unsafe symlink in CSV snapshot")
		}
		if entry.IsDir() {
			return nil
		}
		before, err := entry.Info()
		if err != nil || !before.Mode().IsRegular() {
			return errors.New("unsafe file in CSV snapshot")
		}
		name, err := filepath.Rel(root, path)
		if err != nil || !csvName.MatchString(filepath.ToSlash(name)) {
			return errors.New("unexpected file in CSV snapshot")
		}
		file, err := os.Open(path)
		if err != nil {
			return errors.New("CSV snapshot file unavailable")
		}
		defer file.Close()
		opened, err := file.Stat()
		if err != nil || !os.SameFile(before, opened) || !opened.Mode().IsRegular() {
			return errors.New("CSV snapshot file changed")
		}
		hash := sha256.New()
		_, err = io.Copy(hash, file)
		after, statErr := file.Stat()
		if err != nil || statErr != nil || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
			return errors.New("CSV snapshot changed during hashing")
		}
		rows[filepath.ToSlash(name)] = fmt.Sprintf("%x", hash.Sum(nil))
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, errors.New("CSV snapshot is empty")
	}
	names := make([]string, 0, len(rows))
	for name := range rows {
		names = append(names, name)
	}
	sort.Strings(names)
	var result bytes.Buffer
	for _, name := range names {
		fmt.Fprintf(&result, "%s  %s\n", rows[name], name)
	}
	return result.Bytes(), nil
}

func verifyManifest(path string, expected []byte) error {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("CSV manifest unavailable or unsafe")
	}
	actual, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(actual, expected) {
		return errors.New("CSV snapshot file manifest does not match exact tree")
	}
	return nil
}

func writeManifest(path string, body []byte) error {
	if info, err := os.Lstat(path); err == nil && !info.Mode().IsRegular() || err != nil && !os.IsNotExist(err) {
		return errors.New("CSV manifest output is unsafe")
	}
	file, err := os.CreateTemp(filepath.Dir(path), ".csv-manifest-*")
	if err != nil {
		return errors.New("CSV manifest output unavailable")
	}
	name := file.Name()
	defer os.Remove(name)
	if _, err = file.Write(body); err != nil {
		file.Close()
		return errors.New("CSV manifest write failed")
	}
	if err = file.Chmod(0644); err != nil {
		file.Close()
		return err
	}
	if err = file.Sync(); err != nil {
		file.Close()
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	if err = os.Rename(name, path); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
