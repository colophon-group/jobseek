package worker

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"syscall"
	"time"

	"golang.org/x/sys/unix"
)

var hostServicePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,127}$`)
var hostContainerPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var hostTemporaryPattern = regexp.MustCompile(`^\.host-[0-9a-f]{32}$`)

func hostOwner(info fs.FileInfo, rootAllowed bool) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && (stat.Uid == uint32(os.Geteuid()) || (rootAllowed && stat.Uid == 0))
}

func hostLinks(info fs.FileInfo) uint64 {
	s, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0
	}
	return uint64(s.Nlink)
}

type sharedHostLock struct {
	file     *os.File
	path     string
	identity fs.FileInfo
}

func acquireHostLock(ctx context.Context, path string) (*sharedHostLock, error) {
	if ctx == nil || ctx.Err() != nil || !cleanHostPath(path) {
		return nil, errHostPreflight
	}
	// No truncation; an existing deployment/maintenance flock shares this inode.
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CREAT|unix.O_CLOEXEC|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0600)
	if err != nil {
		return nil, errHostPreflight
	}
	f := os.NewFile(uintptr(fd), "crawler-mutation-lock")
	info, err := f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || !hostOwner(info, true) || hostLinks(info) != 1 {
		f.Close()
		return nil, errHostPreflight
	}
	lock := &sharedHostLock{f, path, info}
	for {
		err = unix.Flock(fd, unix.LOCK_EX|unix.LOCK_NB)
		if err == nil {
			break
		}
		if !errors.Is(err, unix.EWOULDBLOCK) && !errors.Is(err, unix.EAGAIN) {
			f.Close()
			return nil, errHostPreflight
		}
		select {
		case <-ctx.Done():
			f.Close()
			return nil, errHostPreflight
		case <-time.After(25 * time.Millisecond):
		}
	}
	if lock.verify() != nil || ctx.Err() != nil {
		f.Close()
		return nil, errHostPreflight
	}
	return lock, nil
}

func (l *sharedHostLock) Close() error { return l.file.Close() }
func (l *sharedHostLock) verify() error {
	i, err := os.Lstat(l.path)
	opened, openErr := l.file.Stat()
	if err != nil || openErr != nil || !os.SameFile(l.identity, i) || !os.SameFile(i, opened) || i.Mode() != l.identity.Mode() || !hostOwner(i, true) || hostLinks(i) != 1 {
		return errHostPreflight
	}
	return nil
}

type hostStore struct {
	root     *os.Root
	identity fs.FileInfo
	path     string
}

func openHostStore(path string) (*hostStore, error) {
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || resolved != path {
		return nil, errHostPreflight
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode() != os.ModeDir|0700 || !hostOwner(info, false) {
		return nil, errHostPreflight
	}
	root, err := os.OpenRoot(path)
	if err != nil {
		return nil, errHostPreflight
	}
	s := &hostStore{root, info, path}
	if s.verify() != nil {
		root.Close()
		return nil, errHostPreflight
	}
	return s, nil
}

func (s *hostStore) Close() error { return s.root.Close() }
func (s *hostStore) verify() error {
	named, err := os.Lstat(s.path)
	opened, e := s.root.Stat(".")
	if err != nil || e != nil || !os.SameFile(named, s.identity) || !os.SameFile(opened, s.identity) || named.Mode() != s.identity.Mode() || !hostOwner(named, false) {
		return errHostPreflight
	}
	return nil
}

func (s *hostStore) sync() error {
	f, err := s.root.Open(".")
	if err != nil {
		return errHostPreflight
	}
	defer f.Close()
	if f.Sync() != nil || s.verify() != nil {
		return errHostPreflight
	}
	return nil
}

func (s *hostStore) read(name string, pendingLink bool) ([]byte, error) {
	return s.readLimit(name, pendingLink, 8<<20)
}

func (s *hostStore) readLimit(name string, pendingLink bool, limit int64) ([]byte, error) {
	if limit < 1 || limit > 48<<20 || filepath.Base(name) != name || name == "." || name == ".." || s.verify() != nil {
		return nil, errHostPreflight
	}
	before, err := s.root.Lstat(name)
	if err != nil {
		return nil, err
	}
	links := hostLinks(before)
	if before.Mode() != 0600 || !hostOwner(before, false) || before.Size() < 1 || before.Size() > limit || (links != 1 && !(pendingLink && links == 2)) {
		return nil, errHostPreflight
	}
	f, err := s.root.OpenFile(name, os.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, errHostPreflight
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(before, opened) || opened.Mode() != before.Mode() {
		return nil, errHostPreflight
	}
	body, err := io.ReadAll(io.LimitReader(f, limit+1))
	after, e := s.root.Lstat(name)
	if err != nil || e != nil || len(body) != int(before.Size()) || !os.SameFile(before, after) || before.Mode() != after.Mode() || hostLinks(before) != hostLinks(after) || !before.ModTime().Equal(after.ModTime()) || s.verify() != nil {
		return nil, errHostPreflight
	}
	return body, nil
}

// Recover only the exact temp inode from a crash between exclusive hard-link
// publication and temp removal. Unexplained links and substituted files refuse.
func (s *hostStore) finishPublication(name string) error {
	info, err := s.root.Lstat(name)
	if err != nil || info.Mode() != 0600 || !hostOwner(info, false) || hostLinks(info) < 1 || hostLinks(info) > 2 {
		return errHostPreflight
	}
	if hostLinks(info) == 2 {
		dir, err := s.root.Open(".")
		if err != nil {
			return errHostPreflight
		}
		entries, err := dir.ReadDir(257)
		dir.Close()
		if (err != nil && err != io.EOF) || len(entries) > 256 {
			return errHostPreflight
		}
		found := ""
		for _, e := range entries {
			if !hostTemporaryPattern.MatchString(e.Name()) {
				continue
			}
			x, err := s.root.Lstat(e.Name())
			if err != nil {
				return errHostPreflight
			}
			if os.SameFile(x, info) {
				if found != "" || x.Mode() != 0600 || !hostOwner(x, false) {
					return errHostPreflight
				}
				found = e.Name()
			}
		}
		if found == "" || s.root.Remove(found) != nil {
			return errHostPreflight
		}
	}
	// An exact retry must also durably retain independently existing same-byte
	// files; directory fsync alone cannot establish their data durability.
	f, err := s.root.OpenFile(name, os.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK, 0)
	if err != nil {
		return errHostPreflight
	}
	opened, err := f.Stat()
	if err != nil || !os.SameFile(info, opened) || opened.Mode() != info.Mode() || f.Sync() != nil {
		f.Close()
		return errHostPreflight
	}
	if f.Close() != nil {
		return errHostPreflight
	}
	if s.sync() != nil {
		return errHostPreflight
	}
	after, err := s.root.Lstat(name)
	if err != nil || !os.SameFile(info, after) || hostLinks(after) != 1 {
		return errHostPreflight
	}
	return nil
}

func (s *hostStore) retain(name string, body []byte, hook func(string) error) error {
	if len(body) == 0 || len(body) > 8<<20 {
		return errHostPreflight
	}
	if existing, err := s.read(name, true); err == nil {
		if !bytes.Equal(existing, body) {
			return errHostPreflight
		}
		return s.finishPublication(name)
	} else if !errors.Is(err, fs.ErrNotExist) {
		return errHostPreflight
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return errHostPreflight
	}
	tmp := ".host-" + hex.EncodeToString(nonce[:])
	f, err := s.root.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return errHostPreflight
	}
	defer func() { f.Close(); _ = s.root.Remove(tmp) }()
	n, err := f.Write(body)
	if err != nil || n != len(body) || f.Sync() != nil || f.Close() != nil || s.verify() != nil {
		return errHostPreflight
	}
	if hook != nil && hook("file_synced") != nil {
		return errHostPreflight
	}
	if err := s.root.Link(tmp, name); err != nil {
		if !errors.Is(err, fs.ErrExist) {
			return errHostPreflight
		}
		existing, err := s.read(name, true)
		if err != nil || !bytes.Equal(existing, body) {
			return errHostPreflight
		}
	} else if hook != nil && hook("file_linked") != nil {
		return errHostPreflight
	}
	if s.root.Remove(tmp) != nil {
		return errHostPreflight
	}
	return s.finishPublication(name)
}
