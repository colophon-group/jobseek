package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
)

var hostGenerationName = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)
var hostCSVName = regexp.MustCompile(`^[A-Za-z0-9._/-]+\.csv$`)
var hostSnapshotName = regexp.MustCompile(`^run\.[A-Za-z0-9]+$`)
var hostManifestRow = regexp.MustCompile(`^([0-9a-f]{64})  ([A-Za-z0-9._/-]+\.csv)$`)

func regularHostPath(path string) bool {
	info, e := os.Lstat(path)
	return e == nil && info.Mode().IsRegular()
}
func plainHostDirectory(path string) bool {
	info, e := os.Lstat(path)
	return e == nil && info.IsDir() && info.Mode()&os.ModeSymlink == 0
}
func exactHostValue(path, key string) (string, error) {
	if !regularHostPath(path) {
		return "", errors.New("committed evidence unavailable or unsafe")
	}
	raw, e := boundedInventoryFile(path, 16*1024*1024)
	if e != nil {
		return "", errors.New("committed evidence unreadable")
	}
	values := []string{}
	for _, line := range strings.Split(strings.ReplaceAll(string(raw), "\r\n", "\n"), "\n") {
		if strings.HasPrefix(line, key+"=") {
			values = append(values, strings.TrimPrefix(line, key+"="))
		}
	}
	if len(values) != 1 || values[0] == "" {
		return "", errors.New("committed evidence key not unique")
	}
	return values[0], nil
}
func hashHostFile(path string) (string, error) {
	if !regularHostPath(path) {
		return "", errors.New("unsafe registry file")
	}
	f, e := os.Open(path)
	if e != nil {
		return "", e
	}
	defer f.Close()
	h := sha256.New()
	if _, e = io.Copy(h, f); e != nil {
		return "", e
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
func hostRegistryTree(root string) (map[string]string, error) {
	if !plainHostDirectory(root) {
		return nil, errors.New("registry directory unsafe")
	}
	out := map[string]string{}
	e := filepath.WalkDir(root, func(path string, item fs.DirEntry, e error) error {
		if e != nil {
			return e
		}
		if item.Type()&os.ModeSymlink != 0 {
			return errors.New("registry symlink refused")
		}
		if item.IsDir() {
			return nil
		}
		relative, e := filepath.Rel(root, path)
		if e != nil {
			return e
		}
		relative = filepath.ToSlash(relative)
		if !hostCSVName.MatchString(relative) {
			return errors.New("unexpected registry file")
		}
		digest, e := hashHostFile(path)
		if e != nil {
			return e
		}
		out[relative] = digest
		return nil
	})
	return out, e
}
func expectedHostRegistry(generation string) (map[string]string, error) {
	manifest := filepath.Join(generation, "release.manifest")
	version, e := exactHostValue(manifest, "RELEASE_FORMAT_VERSION")
	if e != nil || version != "3" {
		return nil, errors.New("registry requires format-v3 release")
	}
	digest, e := exactHostValue(manifest, "DATA_FILES_SHA256")
	if e != nil || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(digest) {
		return nil, errors.New("invalid registry contract")
	}
	contract, e := exactHostValue(manifest, "DATA_CONTRACT_SHA256")
	if e != nil || contract != digest {
		return nil, errors.New("registry contract differs")
	}
	path := filepath.Join(generation, "data-files.sha256")
	actual, e := hashHostFile(path)
	if e != nil || actual != digest {
		return nil, errors.New("registry manifest digest differs")
	}
	file, e := os.Open(path)
	if e != nil {
		return nil, e
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 4096), 1024*1024)
	out := map[string]string{}
	for scanner.Scan() {
		match := hostManifestRow.FindStringSubmatch(strings.TrimSuffix(scanner.Text(), "\r"))
		if match == nil {
			return nil, errors.New("invalid registry manifest row")
		}
		relative := match[2]
		if filepath.IsAbs(relative) || strings.Contains("/"+relative+"/", "/../") || out[relative] != "" {
			return nil, errors.New("unsafe registry manifest path")
		}
		out[relative] = match[1]
	}
	if e = scanner.Err(); e != nil {
		return nil, e
	}
	if out["companies.csv"] == "" || out["boards.csv"] == "" {
		return nil, errors.New("registry files incomplete")
	}
	return out, nil
}
func prepareHostRegistry(pointer, releaseRoot, snapshotRoot, image, revision string) (snapshot string, err error) {
	if !plainHostDirectory(releaseRoot) {
		return "", errors.New("release root unsafe")
	}
	info, e := os.Lstat(pointer)
	if e != nil || info.Mode()&os.ModeSymlink == 0 {
		return "", errors.New("release pointer unavailable")
	}
	generation, e := os.Readlink(pointer)
	if e != nil || filepath.Dir(generation) != releaseRoot || !hostGenerationName.MatchString(filepath.Base(generation)) || !plainHostDirectory(generation) {
		return "", errors.New("release pointer unsafe")
	}
	for key, want := range map[string]string{"CRAWLER_IMAGE_REF": image, "JOBSEEK_DEPLOY_REVISION": revision} {
		got, e := exactHostValue(filepath.Join(generation, "success.env"), key)
		if e != nil || got != want {
			return "", errors.New("registry release identity differs")
		}
	}
	expected, e := expectedHostRegistry(generation)
	if e != nil {
		return "", e
	}
	data := filepath.Join(generation, "data")
	actual, e := hostRegistryTree(data)
	if e != nil || !reflect.DeepEqual(actual, expected) {
		return "", errors.New("registry differs from exact manifest")
	}
	if !plainHostDirectory(snapshotRoot) {
		return "", errors.New("snapshot root unsafe")
	}
	entries, e := os.ReadDir(snapshotRoot)
	if e != nil {
		return "", e
	}
	for _, entry := range entries {
		if hostSnapshotName.MatchString(entry.Name()) && entry.IsDir() && entry.Type()&os.ModeSymlink == 0 {
			if e = os.RemoveAll(filepath.Join(snapshotRoot, entry.Name())); e != nil {
				return "", e
			}
		}
	}
	snapshot, e = os.MkdirTemp(snapshotRoot, "run.")
	if e != nil {
		return "", e
	}
	defer func() {
		if err != nil {
			os.RemoveAll(snapshot)
		}
	}()
	for relative, digest := range expected {
		target := filepath.Join(snapshot, relative)
		if e = os.MkdirAll(filepath.Dir(target), 0700); e != nil {
			return snapshot, e
		}
		from, e := os.Open(filepath.Join(data, relative))
		if e != nil {
			return snapshot, e
		}
		to, e := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if e != nil {
			from.Close()
			return snapshot, e
		}
		_, e = io.Copy(to, from)
		from.Close()
		if e == nil {
			e = to.Sync()
		}
		closeErr := to.Close()
		if e != nil {
			return snapshot, e
		}
		if closeErr != nil {
			return snapshot, closeErr
		}
		got, e := hashHostFile(target)
		if e != nil || got != digest {
			return snapshot, errors.New("registry snapshot copy differs")
		}
	}
	if e = fsyncInventoryDirectory(snapshot); e != nil {
		return snapshot, e
	}
	return snapshot, nil
}
func runHostRegistry(args []string, output io.Writer) error {
	f := flag.NewFlagSet("host-registry-snapshot", flag.ContinueOnError)
	f.SetOutput(io.Discard)
	pointer := f.String("active-release-pointer", "", "")
	root := f.String("active-release-root", "", "")
	snapshots := f.String("snapshot-root", "", "")
	image := f.String("expected-image", "", "")
	revision := f.String("expected-revision", "", "")
	if e := f.Parse(args); e != nil {
		return e
	}
	if *pointer == "" || *root == "" || *snapshots == "" || *image == "" || *revision == "" || f.NArg() != 0 {
		return errors.New("invalid registry snapshot arguments")
	}
	path, e := prepareHostRegistry(*pointer, *root, *snapshots, *image, *revision)
	if e != nil {
		return e
	}
	_, e = fmt.Fprintln(output, path)
	return e
}
