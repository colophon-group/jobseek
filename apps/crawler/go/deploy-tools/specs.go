package main

import (
	"archive/tar"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

var specDigest = regexp.MustCompile(`^[0-9a-f]{64}$`)
var specMode = regexp.MustCompile(`^[0-7]{3,4}$`)

func safeSpecName(name string) bool {
	if name == "" || strings.Contains(name, "\\") || strings.HasPrefix(name, "/") {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if part == "" || part == "." || part == ".." {
			return false
		}
	}
	return true
}

func packSpecs(snapshot, archive string, entries []string) error {
	root, err := os.OpenRoot(snapshot)
	if err != nil {
		return err
	}
	defer root.Close()
	output, err := regularOpenFlags(archive, os.O_WRONLY)
	if err != nil {
		return err
	}
	defer output.Close()
	if err := output.Truncate(0); err != nil {
		return err
	}
	w := tar.NewWriter(output)
	seen := map[string]bool{}
	for _, name := range entries {
		if !safeSpecName(name) || seen[name] {
			return errors.New("deploy-spec archive member path is unsafe or duplicated")
		}
		seen[name] = true
		before, err := root.Lstat(name)
		if err != nil || !before.Mode().IsRegular() {
			return errors.New("deploy-spec archive member is not regular")
		}
		f, err := root.Open(name)
		if err != nil {
			return err
		}
		opened, err := f.Stat()
		if err != nil || !opened.Mode().IsRegular() || !os.SameFile(before, opened) {
			f.Close()
			return errors.New("deploy-spec member changed")
		}
		h, err := tar.FileInfoHeader(before, "")
		if err != nil {
			f.Close()
			return err
		}
		h.Name, h.Uid, h.Gid, h.Uname, h.Gname, h.Format = name, 0, 0, "", "", tar.FormatUSTAR
		h.ModTime = time.Unix(0, 0)
		h.AccessTime, h.ChangeTime = time.Time{}, time.Time{}
		if err := w.WriteHeader(h); err != nil {
			f.Close()
			return err
		}
		_, copyErr := io.CopyN(w, f, before.Size())
		after, statErr := f.Stat()
		closeErr := f.Close()
		if copyErr != nil {
			return copyErr
		}
		if statErr != nil || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
			return errors.New("deploy-spec member changed during archive")
		}
		if closeErr != nil {
			return closeErr
		}
	}
	if err := w.Close(); err != nil {
		return err
	}
	return output.Sync()
}

type specContent struct {
	body []byte
	mode int64
}

func unpackSpecs(archive, destination, presenceName string, specs []string) error {
	if !safeSpecName(presenceName) {
		return errors.New("unsafe presence member")
	}
	f, err := regularOpen(archive)
	if err != nil {
		return err
	}
	defer f.Close()
	reader := tar.NewReader(f)
	members := map[string]specContent{}
	for {
		h, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if !safeSpecName(h.Name) || (h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA) {
			return errors.New("unsafe rollback member")
		}
		if _, exists := members[h.Name]; exists {
			return errors.New("duplicate rollback member")
		}
		body, err := io.ReadAll(reader)
		if err != nil {
			return err
		}
		members[h.Name] = specContent{body: body, mode: h.Mode}
	}
	presence, ok := members[presenceName]
	if !ok || !utf8.Valid(presence.body) || !strings.HasSuffix(string(presence.body), "\n") {
		return errors.New("rollback presence manifest is unavailable or noncanonical")
	}
	lines := strings.Split(strings.TrimSuffix(string(presence.body), "\n"), "\n")
	if len(lines) != len(specs)+1 || lines[0] != "DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1" {
		return errors.New("rollback presence manifest is incomplete or unsupported")
	}
	expected := map[string]bool{presenceName: true}
	seenSpecs := map[string]bool{}
	for i, name := range specs {
		if !safeSpecName(name) || name == presenceName || seenSpecs[name] {
			return errors.New("unsafe or duplicate rollback spec")
		}
		seenSpecs[name] = true
		parts := strings.SplitN(lines[i+1], " ", 4)
		if len(parts) != 4 || parts[3] != name {
			return errors.New("rollback presence order differs")
		}
		switch parts[0] {
		case "ABSENT":
			if parts[1] != "-" || parts[2] != "-" {
				return errors.New("rollback absence identity is malformed")
			}
		case "PRESENT":
			if !specDigest.MatchString(parts[1]) || !specMode.MatchString(parts[2]) {
				return errors.New("rollback presence identity is malformed")
			}
			var mode uint32
			if _, err := fmt.Sscanf(parts[2], "%o", &mode); err != nil {
				return err
			}
			member, ok := members[name]
			if !ok || fmt.Sprintf("%x", sha256.Sum256(member.body)) != parts[1] || uint32(member.mode)&07777 != mode {
				return errors.New("rollback spec digest or mode differs")
			}
			expected[name] = true
		default:
			return errors.New("rollback presence state is unsupported")
		}
	}
	if len(members) != len(expected) {
		return errors.New("rollback member set differs")
	}
	root, err := os.OpenRoot(destination)
	if err != nil {
		return err
	}
	defer root.Close()
	for _, name := range append(append([]string{}, specs...), presenceName) {
		if !expected[name] {
			continue
		}
		member := members[name]
		if err := root.MkdirAll(filepath.Dir(name), 0700); err != nil {
			return err
		}
		mode := os.FileMode(member.mode) & os.ModePerm
		if member.mode&04000 != 0 {
			mode |= os.ModeSetuid
		}
		if member.mode&02000 != 0 {
			mode |= os.ModeSetgid
		}
		if member.mode&01000 != 0 {
			mode |= os.ModeSticky
		}
		if name == presenceName {
			mode = 0600
		}
		out, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
		if err != nil {
			return err
		}
		_, writeErr := out.Write(member.body)
		if writeErr == nil {
			writeErr = out.Chmod(mode)
		}
		if writeErr == nil {
			writeErr = out.Sync()
		}
		closeErr := out.Close()
		if writeErr != nil {
			return writeErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return syncDirectory(destination)
}
