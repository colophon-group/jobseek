package main

import (
	"archive/tar"
	"bytes"
	"errors"
	"fmt"
	"io"
	"os"
	"path"
	"sort"
	"strings"
)

var errCSVArchive = errors.New("CSV candidate archive evidence rejected")

// The existing registry is under 10MiB. Bound the private snapshot to 128MiB
// and validate its complete tree before extraction can create any file.
func readCSVArchive(archive, contract, revision, runtime string) (map[string][]byte, error) {
	if !bridgeDigest.MatchString(contract) || !bridgeRevision.MatchString(revision) || runtime != "" && !bridgeDigest.MatchString(runtime) {
		return nil, errCSVArchive
	}
	f, err := regularOpen(archive)
	if err != nil {
		return nil, errCSVArchive
	}
	defer f.Close()
	before, err := f.Stat()
	if err != nil {
		return nil, errCSVArchive
	}
	body, err := io.ReadAll(io.LimitReader(f, (128<<20)+1))
	if err != nil || len(body) > 128<<20 {
		return nil, errCSVArchive
	}
	after, err := f.Stat()
	if err != nil || !os.SameFile(before, after) || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return nil, errCSVArchive
	}
	reader := tar.NewReader(bytes.NewReader(body))
	files := map[string][]byte{}
	directories := map[string]bool{}
	rows := map[string]string{}
	for entries := 0; ; entries++ {
		header, err := reader.Next()
		if err == io.EOF {
			break
		}
		if err != nil || entries >= 20000 {
			return nil, errCSVArchive
		}
		if strings.HasPrefix(header.Name, "/") || strings.Contains(header.Name, "\\") {
			return nil, errCSVArchive
		}
		for _, part := range strings.Split(header.Name, "/") {
			if part == ".." {
				return nil, errCSVArchive
			}
		}
		name := path.Clean(header.Name)
		if header.Typeflag == tar.TypeDir {
			if _, exists := files[name]; exists {
				return nil, errCSVArchive
			}
			directories[name] = true
			if name != "data" && !strings.HasPrefix(name, "data/") {
				return nil, errCSVArchive
			}
			continue
		}
		if header.Typeflag != tar.TypeReg && header.Typeflag != tar.TypeRegA || header.Size < 0 || header.Size > 128<<20 {
			return nil, errCSVArchive
		}
		if directories[name] {
			return nil, errCSVArchive
		}
		if _, duplicate := files[name]; duplicate {
			return nil, errCSVArchive
		}
		relative := strings.TrimPrefix(name, "data/")
		if name != "data-files.sha256" && name != "runtime-attestation.env" && (!strings.HasPrefix(name, "data/") || !csvName.MatchString(relative)) {
			return nil, errCSVArchive
		}
		content, err := io.ReadAll(reader)
		if err != nil || int64(len(content)) != header.Size {
			return nil, errCSVArchive
		}
		files[name] = content
		if strings.HasPrefix(name, "data/") {
			rows[relative] = bridgeHash(content)
		}
	}
	// Reject a file/directory collision before creating any destination file.
	for name := range files {
		for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
			if _, exists := files[parent]; exists {
				return nil, errCSVArchive
			}
		}
	}
	if len(rows) == 0 || files["data-files.sha256"] == nil {
		return nil, errCSVArchive
	}
	names := make([]string, 0, len(rows))
	for name := range rows {
		names = append(names, name)
	}
	sort.Strings(names)
	var manifest bytes.Buffer
	for _, name := range names {
		fmt.Fprintf(&manifest, "%s  %s\n", rows[name], name)
	}
	if !bytes.Equal(manifest.Bytes(), files["data-files.sha256"]) || bridgeHash(manifest.Bytes()) != contract {
		return nil, errCSVArchive
	}
	if runtime != "" {
		if _, err := runtimeAttestation(files["runtime-attestation.env"], revision, runtime, ""); err != nil {
			return nil, errCSVArchive
		}
	}
	return files, nil
}

func unpackCSVArchive(archive, destination, contract, revision, runtime string) error {
	files, err := readCSVArchive(archive, contract, revision, runtime)
	if err != nil {
		return err
	}
	info, err := os.Lstat(destination)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return errCSVArchive
	}
	root, err := os.OpenRoot(destination)
	if err != nil {
		return errCSVArchive
	}
	defer root.Close()
	// The prepared generation already contains Compose/env/success only. Refuse
	// all prior archive destinations, including dangling symlinks.
	for _, name := range []string{"data", "data-files.sha256", "runtime-attestation.env"} {
		if _, err := root.Lstat(name); !os.IsNotExist(err) {
			return errCSVArchive
		}
	}
	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)
	for _, name := range names {
		if err := root.MkdirAll(path.Dir(name), 0755); err != nil {
			return errCSVArchive
		}
		f, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
		if err != nil {
			return errCSVArchive
		}
		_, writeErr := f.Write(files[name])
		syncErr := f.Sync()
		closeErr := f.Close()
		if writeErr != nil || syncErr != nil || closeErr != nil {
			return errCSVArchive
		}
	}
	return nil
}
