package releaseevidence

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const specPresenceName = ".jobseek-deploy-spec-presence-v1"
const maxSpecBytes = 4 << 20
const maxSpecArchive = 48 << 20

// This exact order is the deployed ADR006 presence contract. The source test
// compares it with DEPLOY_SPEC_FILES; changing the deployment set requires a
// matching archive protocol review rather than silently ignoring a new file.
var specNames = []string{
	"deploy.sh", "deploy_helpers.sh", "docker-compose.yml",
	"lightpanda-b0-enabled.override.yml", "alloy.river",
	"scripts/postgresql-operational-preflight.py",
	"scripts/lightpanda-claimant-credentials.py", "scripts/lightpanda-b0-cutover.sh",
	"scripts/verify-crawler-release-bridge.py",
}

type specEntry struct {
	Name    string `json:"name"`
	Present bool   `json:"present"`
	SHA256  string `json:"sha256,omitempty"`
	Mode    string `json:"mode,omitempty"`
}
type specDocument struct {
	Version            string      `json:"version"`
	ArchiveSHA256      string      `json:"archive_sha256"`
	PresenceSHA256     string      `json:"presence_sha256"`
	FileEvidenceSHA256 string      `json:"file_evidence_sha256,omitempty"`
	Entries            []specEntry `json:"entries"`
}

// Specs holds verified archive bytes privately. Body is only canonical hashes,
// modes and presence metadata; it never discloses deployment script contents.
// The archive must still be bound to the verified active release and retained
// host intent before any incoming spec selection. No live restore occurs here.
type Specs struct {
	body, digest, archiveHash string
	archive                   []byte
}

func (s *Specs) Body() string          { return s.body }
func (s *Specs) SHA256() string        { return s.digest }
func (s *Specs) ArchiveSHA256() string { return s.archiveHash }

var specModePattern = regexp.MustCompile(`^[0-7]{3,4}$`)
var specArchiveNamePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,120}\.tar$`)

// DecodeSpecArchive authenticates the existing uncompressed USTAR format before
// any extraction/restore. ExpectedSHA256 must come from retained protected host
// intent, not an archive-supplied label. PAX/GNU extensions, links/directories,
// duplicates, unknown members, path aliases and nonzero trailers all refuse.
func DecodeSpecArchive(ctx context.Context, archive []byte, expectedSHA256 string) (*Specs, error) {
	if ctx == nil || ctx.Err() != nil || len(archive) == 0 || len(archive) > maxSpecArchive || len(archive)%512 != 0 || !shaPattern.MatchString(expectedSHA256) || digest(archive) != expectedSHA256 {
		return nil, reject("explicit bounded spec archive hash")
	}
	allowed := map[string]bool{specPresenceName: true}
	for _, name := range specNames {
		allowed[name] = true
	}
	type member struct {
		content []byte
		mode    int64
	}
	members := map[string]member{}
	input := bytes.NewReader(archive)
	r := tar.NewReader(input)
	for {
		if ctx.Err() != nil {
			return nil, reject("spec observation cancelled")
		}
		remaining := input.Len()
		h, err := r.Next()
		if err == io.EOF {
			if remaining-input.Len() < 1024 {
				return nil, reject("complete spec archive end blocks")
			}
			break
		}
		duplicate := false
		if h != nil {
			_, duplicate = members[h.Name]
		}
		if err != nil || h == nil || !allowed[h.Name] || duplicate || (h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA) || h.Format != tar.FormatUSTAR || h.Linkname != "" || h.Size < 0 || h.Size > maxSpecBytes || (h.Name == specPresenceName && h.Size > 64<<10) || h.Mode < 0 || h.Mode > 07777 || len(h.PAXRecords) != 0 {
			return nil, reject("closed regular USTAR spec members")
		}
		content, err := io.ReadAll(io.LimitReader(r, maxSpecBytes+1))
		if err != nil || int64(len(content)) != h.Size || len(content) > maxSpecBytes {
			return nil, reject("bounded complete spec member")
		}
		members[h.Name] = member{content, h.Mode}
	}
	// Python's existing writer pads to 10240 bytes. Preserve that legitimate
	// zero padding while refusing hidden concatenated archives or other payload.
	if input.Len() > 10240 || !bytes.Equal(archive[len(archive)-input.Len():], make([]byte, input.Len())) {
		return nil, reject("spec archive trailer")
	}
	p, ok := members[specPresenceName]
	if !ok || p.mode != 0600 || len(p.content) > 64<<10 || !bytes.HasSuffix(p.content, []byte("\n")) || bytes.ContainsRune(p.content, '\r') {
		return nil, reject("spec presence manifest framing")
	}
	lines, err := lines(p.content)
	if err != nil || len(lines) != len(specNames)+1 || lines[0] != "DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1" {
		return nil, reject("complete ordered spec presence")
	}
	entries := make([]specEntry, 0, len(specNames))
	expectedCount := 1
	for n, name := range specNames {
		fields := strings.Split(lines[n+1], " ")
		if len(fields) != 4 || fields[3] != name {
			return nil, reject("spec presence order")
		}
		m, exists := members[name]
		switch fields[0] {
		case "PRESENT":
			mode, err := strconv.ParseInt(fields[2], 8, 16)
			if !exists || !shaPattern.MatchString(fields[1]) || !specModePattern.MatchString(fields[2]) || err != nil || mode != m.mode || digest(m.content) != fields[1] {
				return nil, reject("spec presence identity")
			}
			entries = append(entries, specEntry{name, true, fields[1], fields[2]})
			expectedCount++
		case "ABSENT":
			if exists || fields[1] != "-" || fields[2] != "-" || name == "docker-compose.yml" {
				return nil, reject("spec absence identity")
			}
			entries = append(entries, specEntry{Name: name})
		default:
			return nil, reject("spec presence state")
		}
	}
	if len(members) != expectedCount || ctx.Err() != nil {
		return nil, reject("exact spec archive membership")
	}
	body, err := json.Marshal(specDocument{Version: "jobseek.crawler-deploy-spec-archive/v1", ArchiveSHA256: expectedSHA256, PresenceSHA256: digest(p.content), Entries: entries})
	if err != nil {
		return nil, reject("canonical spec evidence")
	}
	return &Specs{string(body), digest(body), expectedSHA256, append([]byte{}, archive...)}, nil
}

func unixMode(mode fs.FileMode) int64 {
	n := int64(mode.Perm())
	if mode&os.ModeSetuid != 0 {
		n |= 04000
	}
	if mode&os.ModeSetgid != 0 {
		n |= 02000
	}
	if mode&os.ModeSticky != 0 {
		n |= 01000
	}
	return n
}

type SpecCaptureConfig struct{ DeploymentDirectory, GenerationDirectory, Owner, FileEvidenceSHA256 string }

// CaptureSpecs reads the active fixed spec set, authenticates presence/absence
// and modes twice, and substitutes the verified committed Compose snapshot for
// the mutable live Compose bytes exactly as deploy.sh does. Hold the shared host
// mutation lock. This operation does not select specs or arm host rollback.
func CaptureSpecs(ctx context.Context, c SpecCaptureConfig) (*Specs, error) {
	if ctx == nil || ctx.Err() != nil || !filepath.IsAbs(c.DeploymentDirectory) || !filepath.IsAbs(c.GenerationDirectory) || !shaPattern.MatchString(c.FileEvidenceSHA256) {
		return nil, reject("explicit spec capture inputs")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	f, err := VerifyFiles(ctx, c.GenerationDirectory, c.Owner)
	if err != nil || f.SHA256() != c.FileEvidenceSHA256 {
		return nil, reject("spec active release binding")
	}
	before, err := os.Lstat(c.DeploymentDirectory)
	if err != nil || !before.IsDir() || before.Mode()&os.ModeSymlink != 0 || before.Mode().Perm()&0022 != 0 {
		return nil, reject("protected deployment directory")
	}
	root, err := os.OpenRoot(c.DeploymentDirectory)
	if err != nil {
		return nil, reject("deployment directory open")
	}
	defer root.Close()
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(before, opened) {
		return nil, reject("deployment directory changed")
	}
	checkScripts := func() error {
		info, err := root.Lstat("scripts")
		if errors.Is(err, fs.ErrNotExist) {
			return nil
		}
		if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0022 != 0 {
			return reject("deployment script directory")
		}
		return nil
	}
	if err := checkScripts(); err != nil {
		return nil, err
	}
	r := &reader{ctx: ctx, root: root, hashes: map[string]string{}}
	contents, modes := map[string][]byte{}, map[string]int64{}
	for _, name := range specNames {
		info, err := root.Lstat(name)
		if errors.Is(err, fs.ErrNotExist) {
			continue
		}
		if err != nil || !info.Mode().IsRegular() {
			return nil, reject("active spec type")
		}
		b, err := r.read(name, maxSpecBytes)
		if err != nil {
			return nil, err
		}
		mode := unixMode(info.Mode())
		if !specModePattern.MatchString(fmt.Sprintf("%o", mode)) {
			return nil, reject("active spec mode")
		}
		contents[name], modes[name] = b, mode
	}
	if _, ok := contents["docker-compose.yml"]; !ok {
		return nil, reject("active Compose presence")
	}
	// VerifyFiles has authenticated the full generation before this anchored read;
	// complete file verification is repeated after capture and archive construction.
	gr, err := os.OpenRoot(c.GenerationDirectory)
	if err != nil {
		return nil, reject("committed Compose open")
	}
	defer gr.Close()
	committedReader := &reader{ctx: ctx, root: gr, hashes: map[string]string{}}
	committed, err := committedReader.read("docker-compose.yml", maxSpecBytes)
	if err != nil {
		return nil, err
	}
	var file document
	if json.Unmarshal([]byte(f.Body()), &file) != nil || digest(committed) != file.FileSHA256["docker-compose.yml"] {
		return nil, reject("committed Compose identity")
	}
	var manifest strings.Builder
	manifest.WriteString("DEPLOY_SPEC_PRESENCE_FORMAT_VERSION=1\n")
	for _, name := range specNames {
		b, present := contents[name]
		if !present {
			fmt.Fprintf(&manifest, "ABSENT - - %s\n", name)
			continue
		}
		mode := modes[name]
		if name == "docker-compose.yml" {
			b, mode = committed, 0644
		}
		fmt.Fprintf(&manifest, "PRESENT %s %o %s\n", digest(b), mode, name)
	}
	var output bytes.Buffer
	w := tar.NewWriter(&output)
	put := func(name string, mode int64, content []byte) error {
		if w.WriteHeader(&tar.Header{Name: name, Mode: mode, Size: int64(len(content)), Typeflag: tar.TypeReg, Format: tar.FormatUSTAR, ModTime: time.Unix(0, 0)}) != nil {
			return reject("spec archive header")
		}
		if _, err := w.Write(content); err != nil {
			return reject("spec archive content")
		}
		return nil
	}
	if err := put(specPresenceName, 0600, []byte(manifest.String())); err != nil {
		return nil, err
	}
	for _, name := range specNames {
		b, present := contents[name]
		if !present {
			continue
		}
		mode := modes[name]
		if name == "docker-compose.yml" {
			b, mode = committed, 0644
		}
		if err := put(name, mode, b); err != nil {
			return nil, err
		}
	}
	if w.Close() != nil {
		return nil, reject("spec archive close")
	}
	for _, name := range specNames {
		info, err := root.Lstat(name)
		b, present := contents[name]
		if !present {
			if !errors.Is(err, fs.ErrNotExist) {
				return nil, reject("spec absence readback")
			}
			continue
		}
		if err != nil || !info.Mode().IsRegular() || unixMode(info.Mode()) != modes[name] {
			return nil, reject("spec mode readback")
		}
		readback, err := r.read(name, maxSpecBytes)
		if err != nil || !bytes.Equal(b, readback) {
			return nil, reject("spec content readback")
		}
	}
	if err := checkScripts(); err != nil {
		return nil, err
	}
	again, err := VerifyFiles(ctx, c.GenerationDirectory, c.Owner)
	if err != nil || again.SHA256() != f.SHA256() {
		return nil, reject("spec generation readback")
	}
	after, err := os.Lstat(c.DeploymentDirectory)
	if err != nil || !os.SameFile(before, after) || after.Mode() != before.Mode() {
		return nil, reject("deployment directory readback")
	}
	specs, err := DecodeSpecArchive(ctx, output.Bytes(), digest(output.Bytes()))
	if err != nil {
		return nil, err
	}
	var bound specDocument
	if json.Unmarshal([]byte(specs.Body()), &bound) != nil {
		return nil, reject("spec capture evidence")
	}
	bound.FileEvidenceSHA256 = f.SHA256()
	body, err := json.Marshal(bound)
	if err != nil {
		return nil, reject("bound spec capture evidence")
	}
	specs.body, specs.digest = string(body), digest(body)
	return specs, nil
}

// RetainSpecArchive publishes one immutable mode-0600 archive in an existing
// private directory using fsync + an exclusive hard link + directory fsync.
// Exact existing bytes permit a retry; drift or symlinks refuse without replacing
// them. A retained archive is not by itself an armed rollback or selection grant.
func RetainSpecArchive(ctx context.Context, path string, specs *Specs) error {
	if ctx == nil || ctx.Err() != nil || specs == nil || !filepath.IsAbs(path) || !specArchiveNamePattern.MatchString(filepath.Base(path)) {
		return reject("explicit spec retention inputs")
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if _, err := DecodeSpecArchive(ctx, specs.archive, specs.archiveHash); err != nil {
		return err
	}
	parent := filepath.Dir(path)
	before, err := os.Lstat(parent)
	if err != nil || !before.IsDir() || before.Mode()&os.ModeSymlink != 0 || unixMode(before.Mode()) != 0700 {
		return reject("private spec archive directory")
	}
	root, err := os.OpenRoot(parent)
	if err != nil {
		return reject("spec archive directory open")
	}
	defer root.Close()
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(before, opened) {
		return reject("spec archive directory changed")
	}
	name := filepath.Base(path)
	verifyExisting := func() error {
		info, err := root.Lstat(name)
		if err != nil || !info.Mode().IsRegular() || unixMode(info.Mode()) != 0600 {
			return reject("retained archive type or mode")
		}
		r := &reader{ctx: ctx, root: root, hashes: map[string]string{}}
		b, err := r.read(name, maxSpecArchive)
		if err != nil || digest(b) != specs.archiveHash {
			return reject("retained archive readback")
		}
		return nil
	}
	finish := func() error {
		directory, err := root.Open(".")
		if err != nil {
			return reject("spec archive directory durability")
		}
		syncErr := directory.Sync()
		closeErr := directory.Close()
		after, err := os.Lstat(parent)
		if syncErr != nil || closeErr != nil || err != nil || !os.SameFile(before, after) || after.Mode() != before.Mode() || ctx.Err() != nil {
			return reject("spec archive publication durability")
		}
		return verifyExisting()
	}
	if _, err := root.Lstat(name); err == nil {
		return finish()
	} else if !errors.Is(err, fs.ErrNotExist) {
		return reject("retained archive observation")
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return reject("spec archive temporary identity")
	}
	temporary := ".spec-retain-" + hex.EncodeToString(nonce[:])
	file, err := root.OpenFile(temporary, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return reject("spec archive temporary open")
	}
	defer root.Remove(temporary)
	if err := file.Chmod(0600); err != nil {
		file.Close()
		return reject("spec archive temporary mode")
	}
	_, writeErr := file.Write(specs.archive)
	syncErr := file.Sync()
	closeErr := file.Close()
	if writeErr != nil || syncErr != nil || closeErr != nil || ctx.Err() != nil {
		return reject("spec archive file durability")
	}
	if err := root.Link(temporary, name); err != nil {
		return reject("exclusive spec archive publication")
	}
	if err := root.Remove(temporary); err != nil {
		return reject("spec archive temporary cleanup")
	}
	return finish()
}
