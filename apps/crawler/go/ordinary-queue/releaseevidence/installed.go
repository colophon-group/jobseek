package releaseevidence

import (
	"archive/tar"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"path"
	"regexp"
	"strings"
	"time"
)

// InstalledExpectationSpec is a canonical retained host request. Its digest and
// source/image association must come from independently verified build evidence,
// protected host intent and selected release admission, never container labels.
// This verifier checks the declared bytes; it cannot authenticate that trust root.
type InstalledExpectationSpec struct {
	Version        string            `json:"version"`
	SourceRevision string            `json:"source_revision"`
	ImageID        string            `json:"image_id"`
	Architecture   string            `json:"architecture"`
	Kind           string            `json:"kind"`
	BinarySHA256   string            `json:"binary_sha256"`
	CASHA256       string            `json:"ca_sha256"`
	Assets         map[string]string `json:"asset_sha256"`
	SourceFiles    map[string]string `json:"source_file_sha256,omitempty"`
}

type InstalledExpectation struct {
	spec         InstalledExpectationSpec
	body, digest string
}

func (e *InstalledExpectation) Body() string   { return e.body }
func (e *InstalledExpectation) SHA256() string { return e.digest }

var installedRelativePath = regexp.MustCompile(`^[A-Za-z0-9._/-]{1,512}$`)

func validInstalledPath(name string) bool {
	return installedRelativePath.MatchString(name) && strings.Count(name, "/") <= 16 && !strings.HasPrefix(name, "/") && name != "." && name != ".." && !strings.HasPrefix(name, "../") && path.Clean(name) == name
}

func DecodeInstalledExpectation(body []byte, expectedSHA256 string) (*InstalledExpectation, error) {
	if len(body) == 0 || len(body) > 1<<20 || !shaPattern.MatchString(expectedSHA256) || digest(body) != expectedSHA256 || uniqueJSON(body) != nil {
		return nil, reject("retained installed expectation hash")
	}
	var s InstalledExpectationSpec
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&s) != nil || d.Decode(new(any)) != io.EOF || s.Version != "jobseek.crawler-installed-expectation/v1" || !revisionPattern.MatchString(s.SourceRevision) || !localImageIDPattern.MatchString(s.ImageID) || (s.Architecture != "amd64" && s.Architecture != "arm64") || !shaPattern.MatchString(s.BinarySHA256) || !shaPattern.MatchString(s.CASHA256) || len(s.Assets) == 0 || len(s.Assets)+len(s.SourceFiles) > 4096 {
		return nil, reject("complete installed expectation")
	}
	if (s.Kind != "native-ordinary" && s.Kind != "legacy-python") || (s.Kind == "native-ordinary" && len(s.SourceFiles) != 0) || (s.Kind == "legacy-python" && len(s.SourceFiles) == 0) {
		return nil, reject("explicit installed runtime kind")
	}
	for _, files := range []map[string]string{s.Assets, s.SourceFiles} {
		directories := map[string]bool{}
		for name, hash := range files {
			if !validInstalledPath(name) || !shaPattern.MatchString(hash) {
				return nil, reject("closed installed file identities")
			}
			for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
				if _, conflict := files[parent]; conflict {
					return nil, reject("installed file and directory conflict")
				}
				directories[parent] = true
			}
		}
		if len(directories)+len(files)+1 > 16384 {
			return nil, reject("installed tree member count")
		}
	}
	canonical, err := json.Marshal(s)
	if err != nil || !bytes.Equal(canonical, body) {
		return nil, reject("canonical installed expectation")
	}
	return &InstalledExpectation{s, string(body), expectedSHA256}, nil
}

type InstalledFiles struct{ body, digest string }

func (f *InstalledFiles) Body() string   { return f.body }
func (f *InstalledFiles) SHA256() string { return f.digest }

type installedGroup struct {
	location   string
	files      map[string]string // exact archive-relative names
	executable bool
}

func installedGroups(s InstalledExpectationSpec) []installedGroup {
	binary := "/usr/local/bin/go-ordinary-worker"
	if s.Kind == "legacy-python" {
		binary = "/usr/local/bin/python3.13"
	}
	groups := []installedGroup{
		{binary, map[string]string{path.Base(binary): s.BinarySHA256}, true},
		{"/etc/ssl/certs/ca-certificates.crt", map[string]string{"ca-certificates.crt": s.CASHA256}, false},
	}
	for _, tree := range []struct {
		name  string
		files map[string]string
	}{{"data", s.Assets}, {"src", s.SourceFiles}} {
		if len(tree.files) == 0 {
			continue
		}
		files := map[string]string{}
		for name, hash := range tree.files {
			files[tree.name+"/"+name] = hash
		}
		groups = append(groups, installedGroup{"/app/" + tree.name, files, false})
	}
	return groups
}

const maxInstalledArchive = 256 << 20

type installedArchiveReader struct {
	reader io.Reader
	count  int64
}

func (r *installedArchiveReader) Read(b []byte) (int, error) {
	n, err := r.reader.Read(b)
	r.count += int64(n)
	if r.count > maxInstalledArchive {
		return n, reject("installed archive size")
	}
	return n, err
}

// Consume, hash and discard the daemon's archive; never extract a member, follow
// a link, execute a binary or disclose file bytes. Unknown files and empty extra
// directories refuse. Docker's bounded PAX path/time metadata is supported; links,
// devices, sparse data, xattrs and unsupported extensions refuse.
func verifyInstalledArchive(ctx context.Context, input io.Reader, group installedGroup) error {
	if ctx == nil || input == nil || ctx.Err() != nil {
		return reject("installed archive input")
	}
	r := &installedArchiveReader{reader: input}
	archive := tar.NewReader(r)
	directories := map[string]bool{}
	for name := range group.files {
		for parent := path.Dir(name); parent != "."; parent = path.Dir(parent) {
			directories[parent] = true
		}
	}
	seen := map[string]bool{}
	count := 0
	for {
		before := r.count
		h, err := archive.Next()
		if err == io.EOF {
			if r.count-before < 1024 {
				return reject("complete installed archive end blocks")
			}
			break
		}
		if ctx.Err() != nil || err != nil || h == nil || h.Linkname != "" || h.Size < 0 || h.Size > 64<<20 || h.Mode < 0 || h.Mode&^0777 != 0 || h.Mode&0022 != 0 || (h.Format != tar.FormatUSTAR && h.Format != tar.FormatPAX) {
			return reject("regular protected installed archive members")
		}
		for key, value := range h.PAXRecords {
			if len(value) > 1024 || (key != "path" && key != "mtime" && key != "atime" && key != "ctime") || (key == "path" && value != h.Name) {
				return reject("bounded installed archive metadata")
			}
		}
		name := h.Name
		if h.Typeflag == tar.TypeDir {
			name = strings.TrimSuffix(name, "/")
		}
		if !validInstalledPath(name) || seen[name] || len(seen) > 16384 {
			return reject("unique closed installed archive names")
		}
		seen[name] = true
		if h.Typeflag == tar.TypeDir {
			if !directories[name] || h.Size != 0 {
				return reject("expected installed directory")
			}
			continue
		}
		want, present := group.files[name]
		if !present || (h.Typeflag != tar.TypeReg && h.Typeflag != tar.TypeRegA) || (group.executable && h.Mode&0111 != 0111) {
			return reject("expected installed regular file")
		}
		hash := sha256.New()
		n, err := io.Copy(hash, archive)
		if err != nil || n != h.Size || hex.EncodeToString(hash.Sum(nil)) != want {
			return reject("installed file content identity")
		}
		count++
	}
	// Tar permits padding. Refuse hidden concatenated archives or other payload.
	padding := make([]byte, 10241)
	n, err := io.ReadFull(r, padding)
	if (err != io.EOF && err != io.ErrUnexpectedEOF) || n > 10240 || !bytes.Equal(padding[:n], make([]byte, n)) || count != len(group.files) || ctx.Err() != nil {
		return reject("complete installed archive membership and trailer")
	}
	return nil
}

type dockerArchiveRead func(context.Context, []string, func(io.Reader) error) error

func readDockerArchive(ctx context.Context, args []string, consume func(io.Reader) error) error {
	if ctx == nil || consume == nil {
		return reject("explicit installed archive command")
	}
	bounded, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cmd := dockerReadCommand(bounded, args)
	cmd.Stderr = io.Discard
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return reject("installed archive command pipe")
	}
	defer pipe.Close()
	if cmd.Start() != nil {
		return reject("installed archive command start")
	}
	err = consume(pipe)
	if err != nil {
		cancel()
	}
	waitErr := cmd.Wait()
	if err != nil || waitErr != nil || bounded.Err() != nil {
		return reject("installed archive observation")
	}
	return nil
}

// ObserveInstalledContainerFiles performs fixed read-only image inspect and
// container cp commands. The selected container must already exist; this does
// not create, run, mutate or remove any container. Hold the shared host lock.
// Agents must never invoke this function with a host Docker socket. Source/build
// authentication, effective process identity, mounted-content provenance, full
// security/runtime fidelity and complete cold admission remain separate gates.
func ObserveInstalledContainerFiles(ctx context.Context, inventory *Containers, expected *InstalledExpectation, id string) (*InstalledFiles, error) {
	return observeInstalledContainerFiles(ctx, inventory, expected, id, readDocker, readDockerArchive)
}

func observeInstalledContainerFiles(ctx context.Context, inventory *Containers, expected *InstalledExpectation, id string, run dockerRead, stream dockerArchiveRead) (*InstalledFiles, error) {
	if ctx == nil || ctx.Err() != nil || inventory == nil || expected == nil || run == nil || stream == nil || !containerIDPattern.MatchString(id) || digest([]byte(inventory.Body())) != inventory.SHA256() || !shaPattern.MatchString(inventory.SHA256()) {
		return nil, reject("explicit installed observation evidence")
	}
	bounded, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	validated, err := DecodeInstalledExpectation([]byte(expected.Body()), expected.SHA256())
	if err != nil {
		return nil, err
	}
	s := validated.spec
	found := false
	for _, row := range inventory.rows {
		if row.ID == id && row.ImageID == s.ImageID {
			found = true
		}
	}
	if !found {
		return nil, reject("installed container image binding")
	}
	imageHash := ""
	for pass := 0; pass < 2; pass++ {
		b, err := run(bounded, []string{"image", "inspect", s.ImageID})
		var image []inspectImage
		if err != nil || uniqueJSON(b) != nil || json.Unmarshal(b, &image) != nil || len(image) != 1 || image[0].ID != s.ImageID || image[0].OS != "linux" || image[0].Architecture != s.Architecture || (pass == 1 && digest(b) != imageHash) {
			return nil, reject("installed image identity and platform")
		}
		imageHash = digest(b)
		for _, group := range installedGroups(s) {
			called := false
			var archiveError error
			err := stream(bounded, []string{"cp", id + ":" + group.location, "-"}, func(input io.Reader) error {
				if called {
					archiveError = reject("single installed archive response")
					return archiveError
				}
				called = true
				archiveError = verifyInstalledArchive(bounded, input, group)
				return archiveError
			})
			if archiveError != nil {
				return nil, archiveError // only our closed parser labels
			}
			if err != nil || !called || bounded.Err() != nil {
				return nil, reject("installed file set observation")
			}
		}
	}
	readback, err := observeContainers(bounded, run)
	if err != nil || readback.SHA256() != inventory.SHA256() || bounded.Err() != nil {
		return nil, reject("installed inventory readback")
	}
	body, err := json.Marshal(struct {
		Version            string `json:"version"`
		Scope              string `json:"scope"`
		RuntimeAdmission   bool   `json:"runtime_admission"`
		ExpectationSHA256  string `json:"expectation_sha256"`
		InventorySHA256    string `json:"inventory_sha256"`
		ContainerID        string `json:"container_id"`
		ImageID            string `json:"image_id"`
		ImageInspectSHA256 string `json:"image_inspect_sha256"`
		SourceRevision     string `json:"expected_source_revision"`
		Kind               string `json:"kind"`
		AssetFiles         int    `json:"asset_files"`
		SourceFiles        int    `json:"source_files"`
	}{"jobseek.crawler-installed-container-files/v1", "expected binary/CA/exact asset and optional legacy source file hashes; source association comes from retained host request", false, expected.SHA256(), inventory.SHA256(), id, s.ImageID, imageHash, s.SourceRevision, s.Kind, len(s.Assets), len(s.SourceFiles)})
	if err != nil {
		return nil, reject("canonical installed file evidence")
	}
	return &InstalledFiles{string(body), digest(body)}, nil
}
