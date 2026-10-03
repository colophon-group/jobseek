package releaseevidence

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

// Images is a credential-free observation, not a cold cutover permission. It
// binds file evidence, resolved service images and local Linux image identities.
// Source/binary identity, mounts, users, exclusion and readiness remain required.
type Images struct {
	body, digest          string
	compose, imageInspect []byte // credential-bearing defaults remain private
}

func (i *Images) Body() string   { return i.body }
func (i *Images) SHA256() string { return i.digest }

type ImageObservationConfig struct {
	Directory, Owner, Project, FileEvidenceSHA256, Architecture string
	// ProjectDirectory preserves deployed relative mount/config semantics while
	// Compose/env/override bytes still come from the verified generation. Empty
	// retains generation-directory semantics for existing image-only callers.
	ProjectDirectory string
}

type imageDocument struct {
	Version            string                  `json:"version"`
	FileEvidenceSHA256 string                  `json:"file_evidence_sha256"`
	ComposeSHA256      string                  `json:"resolved_compose_sha256"`
	ComposeBaseSHA256  string                  `json:"compose_base_directory_sha256,omitempty"`
	Project            string                  `json:"project"`
	Architecture       string                  `json:"architecture"`
	Services           map[string]serviceImage `json:"services"`
}
type serviceImage struct {
	Reference string `json:"reference"`
	ImageID   string `json:"image_id"`
}
type composeImages struct {
	Name     string `json:"name"`
	Services map[string]struct {
		Image string          `json:"image"`
		Build json.RawMessage `json:"build"`
	} `json:"services"`
}
type inspectImage struct {
	ID           string   `json:"Id"`
	RepoDigests  []string `json:"RepoDigests"`
	Architecture string   `json:"Architecture"`
	OS           string   `json:"Os"`
}

// References are command arguments, never shell fragments. Restrict their
// grammar as well as their digest; leading flags and interpolation refuse.
var pinnedReferencePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$`)
var servicePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,127}$`)

type dockerRead func(context.Context, []string) ([]byte, error)

// ObserveImages is for the trusted Linux deployment identity, which must hold
// the shared host mutation lock throughout observation and selection. Agents
// must not invoke it against a host Docker socket. Commands are read-only and
// fixed; no pull, run, create, stop, update or selection is performed here.
func ObserveImages(ctx context.Context, c ImageObservationConfig) (*Images, error) {
	return observeImages(ctx, c, readDocker)
}

// Do not embed bytes.Buffer: its promoted ReadFrom would let io.Copy bypass
// this Write bound when os/exec drains the child pipe.
type boundedOutput struct{ buffer bytes.Buffer }

func (b *boundedOutput) Write(p []byte) (int, error) {
	if b.buffer.Len()+len(p) > 8<<20 {
		return 0, reject("Docker observation size bound")
	}
	return b.buffer.Write(p)
}
func (b *boundedOutput) Bytes() []byte { return b.buffer.Bytes() }
func dockerReadCommand(ctx context.Context, args []string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, "/usr/bin/docker", args...)
	// Exclude caller DOCKER_HOST/CONTEXT/CONFIG and Compose interpolation vars.
	// The system CLI/plugin and local daemon belong to the deployment identity.
	// A runner or non-root deployment identity cannot traverse /root. Avoid
	// loading an operator's Docker credentials/context/plugin configuration too;
	// these read-only local commands need only the system CLI/plugin and daemon.
	cmd.Env = []string{"PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/", "DOCKER_CONFIG=/var/empty/jobseek-docker-read", "DOCKER_HOST=unix:///var/run/docker.sock"}
	cmd.Dir = "/"
	cmd.WaitDelay = time.Second
	return cmd
}
func readDocker(ctx context.Context, args []string) ([]byte, error) {
	bounded, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	cmd := dockerReadCommand(bounded, args)
	var output boundedOutput
	cmd.Stdout, cmd.Stderr = &output, io.Discard
	if err := cmd.Run(); err != nil || bounded.Err() != nil {
		return nil, reject("Docker observation failed")
	}
	return output.Bytes(), nil
}

// Docker/Compose permit extra protocol fields. Duplicate JSON keys, malformed
// framing and oversized/nested observations still refuse before any decoding.
func uniqueJSON(b []byte) error {
	if len(b) == 0 || len(b) > 8<<20 || !utf8.Valid(b) {
		return reject("observation size")
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.UseNumber()
	var value func(int) error
	value = func(depth int) error {
		if depth > 64 {
			return reject("observation depth")
		}
		t, err := d.Token()
		if err != nil {
			return reject("observation JSON")
		}
		if delim, ok := t.(json.Delim); ok {
			switch delim {
			case '{':
				keys := map[string]bool{}
				for d.More() {
					k, err := d.Token()
					key, ok := k.(string)
					if err != nil || !ok || keys[key] {
						return reject("duplicate observation key")
					}
					keys[key] = true
					if err := value(depth + 1); err != nil {
						return err
					}
				}
			case '[':
				for d.More() {
					if err := value(depth + 1); err != nil {
						return err
					}
				}
			default:
				return reject("observation framing")
			}
			if _, err := d.Token(); err != nil {
				return reject("observation end")
			}
		}
		return nil
	}
	if err := value(0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return reject("trailing observation")
	}
	return nil
}

func repositoryDigest(ref string) string {
	repository, hash, _ := strings.Cut(ref, "@")
	if colon := strings.LastIndex(repository, ":"); colon > strings.LastIndex(repository, "/") {
		repository = repository[:colon]
	}
	// Docker Hub shorthand and explicit docker.io aliases share RepoDigests.
	repository = strings.TrimPrefix(repository, "docker.io/")
	repository = strings.TrimPrefix(repository, "library/")
	return repository + "@" + hash
}

func observeImages(ctx context.Context, c ImageObservationConfig, run dockerRead) (*Images, error) {
	if ctx == nil || ctx.Err() != nil || run == nil || !filepath.IsAbs(c.Directory) || !ownerPattern.MatchString(c.Owner) || !servicePattern.MatchString(c.Project) || !shaPattern.MatchString(c.FileEvidenceSHA256) || (c.Architecture != "amd64" && c.Architecture != "arm64") {
		return nil, reject("explicit image observation inputs")
	}
	bounded, cancel := context.WithTimeout(ctx, 75*time.Second)
	defer cancel()
	projectDirectory := c.ProjectDirectory
	if projectDirectory == "" {
		projectDirectory = c.Directory
	}
	if !filepath.IsAbs(projectDirectory) || filepath.Clean(projectDirectory) != projectDirectory {
		return nil, reject("explicit Compose base directory")
	}
	before, err := os.Lstat(projectDirectory)
	if err != nil || !before.IsDir() || before.Mode().Perm()&0022 != 0 {
		return nil, reject("protected regular Compose base directory")
	}
	baseRoot, err := os.OpenRoot(projectDirectory)
	if err != nil {
		return nil, reject("Compose base directory open")
	}
	defer baseRoot.Close()
	opened, err := baseRoot.Stat(".")
	if err != nil || !os.SameFile(before, opened) {
		return nil, reject("Compose base directory replaced")
	}
	f, err := VerifyFiles(bounded, c.Directory, c.Owner)
	if err != nil || f.SHA256() != c.FileEvidenceSHA256 {
		return nil, reject("image observation file binding")
	}
	var file document
	if err := json.Unmarshal([]byte(f.Body()), &file); err != nil {
		return nil, reject("verified file evidence")
	}
	args := []string{"compose", "--project-name", c.Project, "--project-directory", projectDirectory, "--env-file", filepath.Join(c.Directory, "environment.env"), "-f", filepath.Join(c.Directory, "docker-compose.yml")}
	if file.FileSHA256["rollback-images.override.yml"] != "" {
		args = append(args, "-f", filepath.Join(c.Directory, "rollback-images.override.yml"))
	}
	args = append(args, "config", "--format", "json")
	compose, err := run(bounded, args)
	if err != nil {
		return nil, reject("Compose command observation")
	}
	if err := uniqueJSON(compose); err != nil {
		return nil, reject("Compose JSON observation")
	}
	var resolved composeImages
	if json.Unmarshal(compose, &resolved) != nil || resolved.Name != c.Project || len(resolved.Services) == 0 || len(resolved.Services) > 128 {
		return nil, reject("Compose service inventory")
	}
	refs := map[string]bool{}
	for name, service := range resolved.Services {
		if !servicePattern.MatchString(name) || !pinnedReferencePattern.MatchString(service.Image) || (len(service.Build) != 0 && string(service.Build) != "null") {
			return nil, reject("immutable Compose service required")
		}
		repository, _, _ := strings.Cut(repositoryDigest(service.Image), "@")
		if expected := f.images[repository]; expected != "" && repositoryDigest(service.Image) != repositoryDigest(expected) {
			return nil, reject("Compose runtime identity differs from release")
		}
		refs[service.Image] = true
	}
	ordered := make([]string, 0, len(refs))
	for ref := range refs {
		ordered = append(ordered, ref)
	}
	sort.Strings(ordered)
	inspectArgs := append([]string{"image", "inspect"}, ordered...)
	observed, err := run(bounded, inspectArgs)
	if err != nil {
		return nil, reject("image inspection command")
	}
	if err := uniqueJSON(observed); err != nil {
		return nil, reject("image inspection JSON")
	}
	var installed []inspectImage
	if json.Unmarshal(observed, &installed) != nil || len(installed) != len(ordered) {
		return nil, reject("image inspection cardinality")
	}
	byReference := map[string]string{}
	for n, image := range installed {
		if !strings.HasPrefix(image.ID, "sha256:") || !shaPattern.MatchString(strings.TrimPrefix(image.ID, "sha256:")) || image.OS != "linux" || image.Architecture != c.Architecture || len(image.RepoDigests) > 128 {
			return nil, reject("installed Linux image identity")
		}
		found := false
		for _, ref := range image.RepoDigests {
			if pinnedReferencePattern.MatchString(ref) && repositoryDigest(ref) == repositoryDigest(ordered[n]) {
				found = true
			}
		}
		if !found {
			return nil, reject("installed repository digest differs")
		}
		byReference[ordered[n]] = image.ID
	}
	// The trusted caller keeps the mutation lock. Read back both commands and
	// the complete file generation to detect concurrent drift before retaining.
	againCompose, err := run(bounded, args)
	if err != nil || !bytes.Equal(compose, againCompose) {
		return nil, reject("Compose readback differs")
	}
	againImages, err := run(bounded, inspectArgs)
	if err != nil || !bytes.Equal(observed, againImages) {
		return nil, reject("image readback differs")
	}
	againFiles, err := VerifyFiles(bounded, c.Directory, c.Owner)
	if err != nil || bounded.Err() != nil || againFiles.SHA256() != f.SHA256() {
		return nil, reject("release file readback differs")
	}
	after, err := os.Lstat(projectDirectory)
	if err != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() || bounded.Err() != nil {
		return nil, reject("Compose base directory readback")
	}
	services := map[string]serviceImage{}
	for name, service := range resolved.Services {
		services[name] = serviceImage{service.Image, byReference[service.Image]}
	}
	body, err := json.Marshal(imageDocument{Version: "jobseek.crawler-release-images/v1", FileEvidenceSHA256: f.SHA256(), ComposeSHA256: digest(compose), ComposeBaseSHA256: digest([]byte(projectDirectory)), Project: c.Project, Architecture: c.Architecture, Services: services})
	if err != nil {
		return nil, reject("canonical image evidence")
	}
	return &Images{body: string(body), digest: digest(body), compose: bytes.Clone(compose), imageInspect: bytes.Clone(observed)}, nil
}
