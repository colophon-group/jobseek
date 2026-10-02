// Package releaseevidence verifies committed crawler release-generation files.
// File verification is one part of the host contract. It does not attest Docker
// image resolution, stopped writers, deployment permission or runtime readiness.
package releaseevidence

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

var ErrInvalid = errors.New("crawler release file evidence rejected")
var shaPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var revisionPattern = regexp.MustCompile(`^[0-9a-f]{40}$`)
var ownerPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
var csvPattern = regexp.MustCompile(`^[A-Za-z0-9._/-]+\.csv$`)

// Files is immutable, canonical, credential-free evidence of the file tree.
// Its digest must never stand in for independent host release admission.
type Files struct {
	body   string
	digest string
}

func (f *Files) Body() string   { return f.body }
func (f *Files) SHA256() string { return f.digest }

type document struct {
	Version               string            `json:"version"`
	DataRevision          string            `json:"data_revision"`
	DeployRevision        string            `json:"deploy_revision"`
	RuntimeContractSHA256 string            `json:"runtime_contract_sha256"`
	BridgeKind            string            `json:"bridge_kind"`
	CSVFiles              int               `json:"csv_files"`
	FileSHA256            map[string]string `json:"file_sha256"`
}

type reader struct {
	ctx    context.Context
	root   *os.Root
	hashes map[string]string
	total  int64
}

func digest(b []byte) string    { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }
func reject(label string) error { return fmt.Errorf("%w: %s", ErrInvalid, label) }

func (r *reader) read(name string, maximum int64) ([]byte, error) {
	if r.ctx.Err() != nil {
		return nil, reject("observation cancelled")
	}
	before, err := r.root.Lstat(name)
	if err != nil || !before.Mode().IsRegular() || before.Size() > maximum {
		return nil, reject("regular bounded file required")
	}
	f, err := r.root.Open(name)
	if err != nil {
		return nil, reject("file open")
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(before, opened) || opened.Mode() != before.Mode() {
		return nil, reject("file replaced")
	}
	b, err := io.ReadAll(io.LimitReader(f, maximum+1))
	if err != nil || int64(len(b)) > maximum {
		return nil, reject("file read bound")
	}
	after, err := r.root.Lstat(name)
	if err != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() || before.Size() != after.Size() || !before.ModTime().Equal(after.ModTime()) {
		return nil, reject("file changed")
	}
	r.total += int64(len(b))
	if r.total > 512<<20 || (r.hashes[name] == "" && len(r.hashes) >= 2048) {
		return nil, reject("generation size bound")
	}
	r.hashes[name] = digest(b)
	return b, nil
}
func (r *reader) exists(name string) bool {
	_, err := r.root.Lstat(name)
	return !errors.Is(err, fs.ErrNotExist)
}
func lines(b []byte) ([]string, error) {
	if !utf8.Valid(b) || bytes.ContainsRune(b, '\x00') {
		return nil, reject("UTF-8 line evidence required")
	}
	result := strings.Split(strings.ReplaceAll(string(b), "\r\n", "\n"), "\n")
	if strings.Contains(strings.Join(result, "\n"), "\r") {
		return nil, reject("line framing")
	}
	if len(result) > 0 && result[len(result)-1] == "" {
		result = result[:len(result)-1]
	}
	return result, nil
}
func exact(l []string, key string) (string, error) {
	value := ""
	count := 0
	for _, line := range l {
		if strings.HasPrefix(line, key+"=") {
			count++
			value = strings.TrimPrefix(line, key+"=")
		}
	}
	if count != 1 || value == "" {
		return "", reject("missing or duplicate identity key")
	}
	return value, nil
}
func noKey(l []string, key string) bool {
	for _, line := range l {
		if strings.HasPrefix(line, key+"=") {
			return false
		}
	}
	return true
}
func hashed(r *reader, name, hash string, maximum int64) ([]byte, error) {
	if !shaPattern.MatchString(hash) {
		return nil, reject("malformed file hash")
	}
	b, err := r.read(name, maximum)
	if err != nil {
		return nil, err
	}
	if digest(b) != hash {
		return nil, reject("file hash differs")
	}
	return b, nil
}
func image(owner, name, value string) bool {
	prefix := "ghcr.io/" + owner + "/" + name + "@sha256:"
	return strings.HasPrefix(value, prefix) && shaPattern.MatchString(strings.TrimPrefix(value, prefix))
}

// VerifyFiles reads one explicit format-v3 generation through an anchored root.
// It never sources its environment or runs a command. Unknown top-level files,
// symlinks, duplicate manifest identities and unattested override/bridge residue
// refuse. Docker and all-writer checks remain separate mandatory host phases.
func VerifyFiles(ctx context.Context, directory, owner string) (*Files, error) {
	if ctx.Err() != nil || !filepath.IsAbs(directory) || !ownerPattern.MatchString(owner) {
		return nil, reject("explicit generation and owner required")
	}
	before, err := os.Lstat(directory)
	if err != nil || !before.IsDir() || before.Mode()&os.ModeSymlink != 0 || before.Mode().Perm() != 0700 {
		return nil, reject("private generation directory required")
	}
	root, err := os.OpenRoot(directory)
	if err != nil {
		return nil, reject("generation open")
	}
	defer root.Close()
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(before, opened) {
		return nil, reject("generation replaced")
	}
	r := &reader{ctx: ctx, root: root, hashes: map[string]string{}}
	manifest, err := r.read("release.manifest", 64<<10)
	if err != nil {
		return nil, err
	}
	ml, err := lines(manifest)
	if err != nil {
		return nil, err
	}
	m := map[string]string{}
	for _, line := range ml {
		key, value, ok := strings.Cut(line, "=")
		if !ok || key == "" || value == "" || m[key] != "" {
			return nil, reject("closed manifest fields required")
		}
		m[key] = value
	}
	allowed := map[string]bool{}
	for _, k := range []string{"RELEASE_FORMAT_VERSION", "COMPOSE_SHA256", "ENVIRONMENT_SHA256", "SUCCESS_SHA256", "DATA_FILES_SHA256", "DATA_CONTRACT_SHA256", "DATA_REVISION", "HAS_IMAGE_OVERRIDE", "IMAGE_OVERRIDE_SHA256", "BOOTSTRAP_LEGACY", "LEGACY_BRIDGE_FORMAT_VERSION", "LEGACY_BRIDGE_TRANSITIVE", "LEGACY_RUNTIME_ATTESTATION_SHA256", "LEGACY_SOURCE_RELEASE_FORMAT", "LEGACY_SOURCE_REVISION", "LEGACY_SOURCE_CRAWLER_IMAGE_REF", "LEGACY_SOURCE_COMPOSE_SHA256", "LEGACY_SOURCE_ENVIRONMENT_SHA256", "LEGACY_SOURCE_SUCCESS_SHA256", "LEGACY_SOURCE_IMAGE_OVERRIDE_SHA256"} {
		allowed[k] = true
	}
	for key := range m {
		if !allowed[key] {
			return nil, reject("unexpected manifest field")
		}
	}
	if m["RELEASE_FORMAT_VERSION"] != "3" || !revisionPattern.MatchString(m["DATA_REVISION"]) || !shaPattern.MatchString(m["DATA_FILES_SHA256"]) || m["DATA_FILES_SHA256"] != m["DATA_CONTRACT_SHA256"] {
		return nil, reject("format-v3 data identity")
	}
	compose, err := hashed(r, "docker-compose.yml", m["COMPOSE_SHA256"], 4<<20)
	if err != nil {
		return nil, err
	}
	envInfo, err := root.Lstat("environment.env")
	if err != nil || envInfo.Mode().Perm() != 0600 {
		return nil, reject("environment mode")
	}
	env, err := hashed(r, "environment.env", m["ENVIRONMENT_SHA256"], 4<<20)
	if err != nil {
		return nil, err
	}
	success, err := hashed(r, "success.env", m["SUCCESS_SHA256"], 64<<10)
	if err != nil {
		return nil, err
	}
	for _, snapshot := range []struct{ name, hash string }{{"docker-compose.sha256", m["COMPOSE_SHA256"]}, {"environment.sha256", m["ENVIRONMENT_SHA256"]}} {
		b, err := r.read(snapshot.name, 256)
		if err != nil || strings.TrimSpace(string(b)) != snapshot.hash {
			return nil, reject("snapshot digest file")
		}
	}
	el, err := lines(env)
	if err != nil {
		return nil, err
	}
	sl, err := lines(success)
	if err != nil {
		return nil, err
	}
	ids := map[string]string{}
	for _, key := range []string{"CRAWLER_IMAGE_TAG", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF", "JOBSEEK_DEPLOY_REVISION", "JOBSEEK_RUNTIME_CONTRACT_SHA256"} {
		v, err := exact(el, key)
		s, serr := exact(sl, key)
		if err != nil || serr != nil || v != s {
			return nil, reject("environment/success identity differs")
		}
		ids[key] = v
	}
	if !revisionPattern.MatchString(ids["JOBSEEK_DEPLOY_REVISION"]) || !shaPattern.MatchString(ids["JOBSEEK_RUNTIME_CONTRACT_SHA256"]) || !image(owner, "jobseek-crawler", ids["CRAWLER_IMAGE_REF"]) || !image(owner, "jobseek-crawler-browser", ids["BROWSER_IMAGE_REF"]) {
		return nil, reject("immutable runtime image identity")
	}
	if m["BOOTSTRAP_LEGACY"] != "" && (m["BOOTSTRAP_LEGACY"] != "0" || m["HAS_IMAGE_OVERRIDE"] != "1") {
		return nil, reject("format-v3 bootstrap residue")
	}
	if m["HAS_IMAGE_OVERRIDE"] == "1" {
		if _, err := hashed(r, "rollback-images.override.yml", m["IMAGE_OVERRIDE_SHA256"], 4<<20); err != nil {
			return nil, err
		}
	} else if m["HAS_IMAGE_OVERRIDE"] != "0" || m["IMAGE_OVERRIDE_SHA256"] != "" || r.exists("rollback-images.override.yml") {
		return nil, reject("override presence contract")
	}
	dataManifest, err := hashed(r, "data-files.sha256", m["DATA_FILES_SHA256"], 256<<10)
	if err != nil {
		return nil, err
	}
	dataInfo, err := root.Lstat("data")
	if err != nil || !dataInfo.IsDir() || dataInfo.Mode()&os.ModeSymlink != 0 {
		return nil, reject("CSV directory")
	}
	csvRows := []string{}
	err = fs.WalkDir(root.FS(), "data", func(name string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil || entry.Type()&os.ModeSymlink != 0 {
			return reject("unsafe CSV tree")
		}
		if entry.IsDir() {
			return nil
		}
		relative := strings.TrimPrefix(name, "data/")
		if !fs.ValidPath(relative) || !csvPattern.MatchString(relative) {
			return reject("unexpected CSV path")
		}
		b, err := r.read(name, 64<<20)
		if err != nil {
			return err
		}
		csvRows = append(csvRows, relative+"\x00"+digest(b))
		return nil
	})
	if err != nil {
		return nil, err
	}
	if len(csvRows) == 0 {
		return nil, reject("empty CSV tree")
	}
	sort.Strings(csvRows)
	var csv bytes.Buffer
	for _, row := range csvRows {
		name, hash, _ := strings.Cut(row, "\x00")
		fmt.Fprintf(&csv, "%s  %s\n", hash, name)
	}
	if !bytes.Equal(csv.Bytes(), dataManifest) {
		return nil, reject("exact CSV manifest differs")
	}
	kind, err := verifyBridge(r, owner, m, el, sl, env, success, compose)
	if err != nil {
		return nil, err
	}
	entries, err := fs.ReadDir(root.FS(), ".")
	if err != nil {
		return nil, reject("generation entries")
	}
	for _, entry := range entries {
		if entry.Name() == "data" && entry.IsDir() {
			continue
		}
		if entry.Type()&os.ModeSymlink != 0 || entry.IsDir() || r.hashes[entry.Name()] == "" {
			return nil, reject("unattested generation entry")
		}
	}
	// Reobserve every independently hashed file before returning a retained
	// identity. The caller must still hold the host mutation lock throughout
	// verification and selection; this is not an unlocked snapshot protocol.
	expectedHashes := map[string]string{}
	for name, hash := range r.hashes {
		expectedHashes[name] = hash
	}
	for name, hash := range expectedHashes {
		b, err := r.read(name, 64<<20)
		if err != nil || digest(b) != hash {
			return nil, reject("file readback differs")
		}
	}
	envInfo, err = root.Lstat("environment.env")
	if err != nil || envInfo.Mode().Perm() != 0600 {
		return nil, reject("environment mode readback")
	}
	after, err := os.Lstat(directory)
	if err != nil || ctx.Err() != nil || !os.SameFile(before, after) || after.Mode().Perm() != 0700 {
		return nil, reject("generation moved")
	}
	b, err := json.Marshal(document{"jobseek.crawler-release-files/v1", m["DATA_REVISION"], ids["JOBSEEK_DEPLOY_REVISION"], ids["JOBSEEK_RUNTIME_CONTRACT_SHA256"], kind, len(csvRows), r.hashes})
	if err != nil {
		return nil, reject("canonical evidence")
	}
	return &Files{string(b), digest(b)}, nil
}
