package claimantbundle

import (
	"archive/tar"
	"bytes"
	"crypto/subtle"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"syscall"
	"time"
)

var fileModes = map[string]os.FileMode{"ca.pem": 0444, "client.pem": 0444, "client-key.pem": 0400, "ca.sha256": 0444, "server-leaf.sha256": 0444, "server-spki.sha256": 0444}
var revisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
var digestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)

func noFollowRead(path string, expected os.FileInfo, maximum int64) ([]byte, error) {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	before, err := f.Stat()
	if err != nil || !before.Mode().IsRegular() || !os.SameFile(expected, before) || before.Size() != expected.Size() || before.Mode() != expected.Mode() || before.Size() <= 0 || before.Size() > maximum {
		return nil, reject("file changed before read")
	}
	body, err := io.ReadAll(io.LimitReader(f, maximum+1))
	if err != nil {
		return nil, err
	}
	after, err := f.Stat()
	if err != nil || len(body) != int(before.Size()) || !os.SameFile(before, after) || before.Size() != after.Size() || before.Mode() != after.Mode() || !before.ModTime().Equal(after.ModTime()) || changedTime(before) != changedTime(after) {
		return nil, reject("file changed during read")
	}
	return body, nil
}

func archivePayloads(body []byte) (map[string][]byte, error) {
	payloads := map[string][]byte{}
	r := tar.NewReader(bytes.NewReader(body))
	for {
		h, err := r.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, reject("unreadable archive")
		}
		mode, ok := fileModes[h.Name]
		if h.Name == "server.pem" {
			mode, ok = 0444, true
		}
		if !ok || payloads[h.Name] != nil || (h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA) || h.Mode != int64(mode) {
			return nil, reject("archive member path, type or mode")
		}
		maximum := int64(65)
		if filepath.Ext(h.Name) == ".pem" {
			maximum = 32 * 1024
		}
		if h.Size <= 0 || h.Size > maximum {
			return nil, reject("archive member size")
		}
		payload, err := io.ReadAll(io.LimitReader(r, maximum+1))
		if err != nil || len(payload) != int(h.Size) {
			return nil, reject("incomplete archive member")
		}
		if maximum == 65 {
			if len(payload) != 65 || payload[64] != '\n' || !digestPattern.Match(payload[:64]) {
				return nil, reject("noncanonical pin")
			}
		} else if bytes.IndexByte(payload, 0) >= 0 {
			return nil, reject("invalid PEM")
		}
		payloads[h.Name] = payload
	}
	if len(payloads) != 7 {
		return nil, reject("incomplete member set")
	}
	return payloads, nil
}

func syncDirectory(path string) error {
	f, err := os.OpenFile(path, os.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW, 0)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}

func verifyGeneration(path string, payloads map[string][]byte) error {
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm() != 0711 {
		return reject("generation directory")
	}
	entries, err := os.ReadDir(path)
	if err != nil || len(entries) != len(fileModes) {
		return reject("generation member set")
	}
	for _, entry := range entries {
		mode, ok := fileModes[entry.Name()]
		if !ok {
			return reject("generation member set")
		}
		file := filepath.Join(path, entry.Name())
		metadata, err := os.Lstat(file)
		if err != nil || !metadata.Mode().IsRegular() || metadata.Mode().Perm() != mode || metadata.Size() != int64(len(payloads[entry.Name()])) {
			return reject("generation file metadata")
		}
		sealed := false
		if entry.Name() == "client-key.pem" {
			st, ok := metadata.Sys().(*syscall.Stat_t)
			if !ok {
				return reject("private key metadata")
			}
			sealed = st.Uid == 10001 && st.Gid == 10001
			installer := st.Uid == uint32(os.Geteuid()) && st.Gid == uint32(os.Getegid())
			if !sealed && !installer {
				return reject("private key ownership")
			}
		}
		body, err := noFollowRead(file, metadata, 32*1024)
		if os.IsPermission(err) && sealed {
			continue
		}
		if err != nil || !bytes.Equal(body, payloads[entry.Name()]) {
			return reject("generation content changed")
		}
	}
	return nil
}

// Prepare preserves the original content-addressed, six-file generation and
// sealed claimant-owned key retry contract. It never prints credential bytes.
func Prepare(archive, expectedSHA, revision, root, host string) (string, error) {
	return prepare(archive, expectedSHA, revision, root, host, syncDirectory)
}

func prepare(archive, expectedSHA, revision, root, host string, syncDir func(string) error) (string, error) {
	if !revisionPattern.MatchString(revision) || !digestPattern.MatchString(expectedSHA) {
		return "", reject("revision or archive digest")
	}
	metadata, err := os.Lstat(archive)
	if err != nil || !metadata.Mode().IsRegular() || metadata.Mode().Perm() != 0600 || metadata.Size() <= 0 || metadata.Size() > 256*1024 {
		return "", reject("archive metadata")
	}
	body, err := noFollowRead(archive, metadata, 256*1024)
	if err != nil {
		return "", reject("archive read")
	}
	if subtle.ConstantTimeCompare([]byte(digest(body)), []byte(expectedSHA)) != 1 {
		return "", reject("archive digest mismatch")
	}
	payloads, err := archivePayloads(body)
	if err != nil {
		return "", err
	}
	if err = validateMaterial(payloads, host, time.Now().UTC()); err != nil {
		return "", err
	}
	if info, err := os.Lstat(root); err == nil {
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return "", reject("credential root")
		}
	} else if !os.IsNotExist(err) {
		return "", reject("credential root")
	} else if err = os.MkdirAll(root, 0700); err != nil {
		return "", reject("credential root creation")
	}
	if err = os.Chmod(root, 0700); err != nil {
		return "", reject("credential root mode")
	}
	generation := filepath.Join(root, "sha-"+revision+"-"+expectedSHA)
	if _, err = os.Lstat(generation); err == nil {
		return generation, verifyGeneration(generation, payloads)
	} else if !os.IsNotExist(err) {
		return "", reject("generation lookup")
	}
	candidate, err := os.MkdirTemp(root, ".candidate-")
	if err != nil {
		return "", reject("candidate directory")
	}
	defer os.RemoveAll(candidate)
	if err = os.Chmod(candidate, 0711); err != nil {
		return "", reject("candidate mode")
	}
	for name, mode := range fileModes {
		f, err := os.OpenFile(filepath.Join(candidate, name), os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, mode)
		if err != nil {
			return "", reject("candidate file creation")
		}
		n, writeErr := f.Write(payloads[name])
		syncErr := f.Sync()
		modeErr := f.Chmod(mode)
		closeErr := f.Close()
		if writeErr != nil || n != len(payloads[name]) || syncErr != nil || modeErr != nil || closeErr != nil {
			return "", reject("candidate write or fsync")
		}
	}
	if err = verifyGeneration(candidate, payloads); err != nil {
		return "", err
	}
	if syncDir(candidate) != nil || os.Rename(candidate, generation) != nil || syncDir(root) != nil {
		return "", reject("generation publication")
	}
	return generation, verifyGeneration(generation, payloads)
}
