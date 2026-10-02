package worker

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// This is the connected host preflight phase, before writer containment or
// incoming-spec selection. It must never become a cold grant by renaming its
// receipt. SQL barriers, authenticated build/selection, full runtime fidelity,
// all-writer exclusion and readiness remain mandatory subsequent host phases.
var errHostPreflight = errors.New("ordinary host preflight rejected")

const hostMutationLock = "/run/lock/jobseek-crawler-mutation.lock"

type HostReleaseRequest struct {
	Role               string `json:"role"`
	Directory          string `json:"directory"`
	FileEvidenceSHA256 string `json:"file_evidence_sha256"`
}

type HostInstalledRequest struct {
	Role        string                           `json:"role"`
	Service     string                           `json:"service"`
	ContainerID string                           `json:"container_id"`
	Expectation release.InstalledExpectationSpec `json:"expectation"`
}

// Hashes bind the protected operator request; they do not authenticate its
// image build or prove that a requested role is the host's selected generation.
type HostPreflightRequest struct {
	Version             string                 `json:"version"`
	CoordinatorSource   string                 `json:"coordinator_source_revision"`
	Owner               string                 `json:"owner"`
	Project             string                 `json:"project"`
	Architecture        string                 `json:"architecture"`
	DeploymentDirectory string                 `json:"deployment_directory"`
	Releases            []HostReleaseRequest   `json:"releases"`
	Installed           []HostInstalledRequest `json:"installed"`
}

type HostPreflightConfig struct{ directory, expected, source string }

func ReadHostPreflightConfig(getenv func(string) string, source string) (HostPreflightConfig, error) {
	if getenv == nil || !sourcePattern.MatchString(source) || getenv("ORDINARY_GO_WORKER_MODE") != "host-preflight" || getenv("ORDINARY_HOST_COORDINATOR_SOURCE_REVISION") != source {
		return HostPreflightConfig{}, errHostPreflight
	}
	c := HostPreflightConfig{getenv("ORDINARY_HOST_REQUEST_DIRECTORY"), getenv("ORDINARY_HOST_REQUEST_SHA256"), source}
	if !cleanHostPath(c.directory) || !planPattern.MatchString(c.expected) {
		return HostPreflightConfig{}, errHostPreflight
	}
	return c, nil
}

func cleanHostPath(p string) bool {
	return filepath.IsAbs(p) && filepath.Clean(p) == p && p != "/" && len(p) <= 4096 && !strings.ContainsRune(p, 0)
}

func hostDigest(b []byte) string { h := sha256.Sum256(b); return hex.EncodeToString(h[:]) }

func decodeHostRequest(body []byte, expected, source string) (HostPreflightRequest, error) {
	var r HostPreflightRequest
	if len(body) == 0 || len(body) > 8<<20 || !planPattern.MatchString(expected) || hostDigest(body) != expected {
		return r, errHostPreflight
	}
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || d.Decode(new(any)) != io.EOF || r.Version != "jobseek.crawler-host-preflight-request/v1" || r.CoordinatorSource != source || !sourcePattern.MatchString(source) || !releaseOwnerPattern.MatchString(r.Owner) || !hostServicePattern.MatchString(r.Project) || (r.Architecture != "amd64" && r.Architecture != "arm64") || !cleanHostPath(r.DeploymentDirectory) || len(r.Releases) != 3 || r.Installed == nil || len(r.Installed) > 128 {
		return HostPreflightRequest{}, errHostPreflight
	}
	for n, role := range []string{"active", "incoming", "rollback"} {
		g := r.Releases[n]
		if g.Role != role || !cleanHostPath(g.Directory) || !planPattern.MatchString(g.FileEvidenceSHA256) {
			return HostPreflightRequest{}, errHostPreflight
		}
	}
	seen := map[string]bool{}
	for _, item := range r.Installed {
		if (item.Role != "active" && item.Role != "incoming" && item.Role != "rollback") || !hostServicePattern.MatchString(item.Service) || !hostContainerPattern.MatchString(item.ContainerID) || seen[item.ContainerID] || item.Expectation.Architecture != r.Architecture {
			return HostPreflightRequest{}, errHostPreflight
		}
		seen[item.ContainerID] = true
		b, err := json.Marshal(item.Expectation)
		if err != nil {
			return HostPreflightRequest{}, errHostPreflight
		}
		if _, err := release.DecodeInstalledExpectation(b, hostDigest(b)); err != nil {
			return HostPreflightRequest{}, errHostPreflight
		}
	}
	canonical, err := json.Marshal(r)
	// Re-encoding rejects duplicates, unknown/case aliases and noncanonical
	// framing throughout embedded expectations, without interpreting evidence.
	if err != nil || !bytes.Equal(body, canonical) {
		return HostPreflightRequest{}, errHostPreflight
	}
	return r, nil
}

type hostReleaseObservation struct {
	Role      string          `json:"role"`
	Files     json.RawMessage `json:"files"`
	FilesSHA  string          `json:"files_sha256"`
	Images    json.RawMessage `json:"images"`
	ImagesSHA string          `json:"images_sha256"`
}

type hostObservations struct {
	Releases  []hostReleaseObservation
	Inventory *release.Containers
	Execution *release.Execution
	Installed []*release.InstalledFiles
	Specs     *release.Specs
}

// Observe every requested generation under one lock and one daemon inventory.
// Installed requests, when supplied, must join the exact service image and
// generation source, rather than becoming unattached image-file receipts.
func observeHostPreflight(ctx context.Context, r HostPreflightRequest) (*hostObservations, error) {
	o := &hostObservations{Installed: []*release.InstalledFiles{}}
	images := []*release.Images{}
	byRole := map[string]*release.Images{}
	sources := map[string]string{}
	seen := map[string]bool{}
	for _, g := range r.Releases {
		f, err := release.VerifyFiles(ctx, g.Directory, r.Owner)
		if err != nil || f.SHA256() != g.FileEvidenceSHA256 {
			return nil, errHostPreflight
		}
		var file struct {
			DeployRevision string `json:"deploy_revision"`
		}
		if json.Unmarshal([]byte(f.Body()), &file) != nil || !sourcePattern.MatchString(file.DeployRevision) {
			return nil, errHostPreflight
		}
		i, err := release.ObserveImages(ctx, release.ImageObservationConfig{Directory: g.Directory, Owner: r.Owner, Project: r.Project, FileEvidenceSHA256: f.SHA256(), Architecture: r.Architecture, ProjectDirectory: r.DeploymentDirectory})
		if err != nil {
			return nil, errHostPreflight
		}
		byRole[g.Role], sources[g.Role] = i, file.DeployRevision
		if !seen[i.SHA256()] {
			images = append(images, i)
			seen[i.SHA256()] = true
		}
		o.Releases = append(o.Releases, hostReleaseObservation{g.Role, json.RawMessage(f.Body()), f.SHA256(), json.RawMessage(i.Body()), i.SHA256()})
	}
	var err error
	o.Inventory, err = release.ObserveContainers(ctx)
	if err != nil {
		return nil, errHostPreflight
	}
	o.Execution, err = release.RequireContainerExecution(ctx, o.Inventory, images)
	if err != nil {
		return nil, errHostPreflight
	}
	for _, item := range r.Installed {
		var inventory struct {
			Containers []struct {
				ID, Project, Service string
				Oneoff               bool `json:"oneoff"`
				OneoffKnown          bool `json:"oneoff_known"`
			} `json:"containers"`
		}
		if json.Unmarshal([]byte(o.Inventory.Body()), &inventory) != nil {
			return nil, errHostPreflight
		}
		bound := false
		for _, row := range inventory.Containers {
			if row.ID == item.ContainerID && row.Project == r.Project && row.Service == item.Service && row.OneoffKnown && !row.Oneoff {
				bound = true
			}
		}
		if !bound {
			return nil, errHostPreflight
		}
		var bindings struct {
			Services map[string]struct {
				ImageID string `json:"image_id"`
			} `json:"services"`
		}
		if json.Unmarshal([]byte(byRole[item.Role].Body()), &bindings) != nil || bindings.Services[item.Service].ImageID != item.Expectation.ImageID || sources[item.Role] != item.Expectation.SourceRevision {
			return nil, errHostPreflight
		}
		body, _ := json.Marshal(item.Expectation)
		expected, err := release.DecodeInstalledExpectation(body, hostDigest(body))
		if err != nil {
			return nil, errHostPreflight
		}
		files, err := release.ObserveInstalledContainerFiles(ctx, o.Inventory, expected, item.ContainerID)
		if err != nil {
			return nil, errHostPreflight
		}
		o.Installed = append(o.Installed, files)
	}
	a := r.Releases[0]
	o.Specs, err = release.CaptureSpecs(ctx, release.SpecCaptureConfig{DeploymentDirectory: r.DeploymentDirectory, GenerationDirectory: a.Directory, Owner: r.Owner, FileEvidenceSHA256: a.FileEvidenceSHA256})
	if err != nil {
		return nil, errHostPreflight
	}
	// Complete readback follows all installed-file/spec observations, under the
	// lock. An earlier per-library readback alone is not this connected boundary.
	for n, g := range r.Releases {
		i, err := release.ObserveImages(ctx, release.ImageObservationConfig{Directory: g.Directory, Owner: r.Owner, Project: r.Project, FileEvidenceSHA256: g.FileEvidenceSHA256, Architecture: r.Architecture, ProjectDirectory: r.DeploymentDirectory})
		if err != nil || i.SHA256() != o.Releases[n].ImagesSHA {
			return nil, errHostPreflight
		}
	}
	again, err := release.ObserveContainers(ctx)
	if err != nil || again.SHA256() != o.Inventory.SHA256() || ctx.Err() != nil {
		return nil, errHostPreflight
	}
	return o, nil
}

type HostPreflightResult struct {
	Version          string          `json:"version"`
	Operation        string          `json:"operation"`
	SourceRevision   string          `json:"source_revision"`
	Phase            string          `json:"phase"`
	RuntimeAdmission bool            `json:"runtime_admission"`
	RequestSHA256    string          `json:"request_sha256"`
	IntentSHA256     string          `json:"intent_sha256"`
	ReceiptSHA256    string          `json:"receipt_sha256"`
	Receipt          json.RawMessage `json:"receipt"`
}

func RunHostPreflight(ctx context.Context, c HostPreflightConfig) (*HostPreflightResult, error) {
	if runtime.GOOS != "linux" {
		return nil, errHostPreflight
	}
	return runHostPreflight(ctx, c, hostMutationLock, observeHostPreflight, nil)
}

func runHostPreflight(ctx context.Context, c HostPreflightConfig, lockPath string, observe func(context.Context, HostPreflightRequest) (*hostObservations, error), hook func(string) error) (*HostPreflightResult, error) {
	if ctx == nil || ctx.Err() != nil || !cleanHostPath(c.directory) || !planPattern.MatchString(c.expected) || !sourcePattern.MatchString(c.source) || observe == nil {
		return nil, errHostPreflight
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	lock, err := acquireHostLock(ctx, lockPath)
	if err != nil {
		return nil, errHostPreflight
	}
	defer lock.Close()
	store, err := openHostStore(c.directory)
	if err != nil {
		return nil, errHostPreflight
	}
	defer store.Close()
	body, err := store.read("request.json", false)
	if err != nil {
		return nil, errHostPreflight
	}
	r, err := decodeHostRequest(body, c.expected, c.source)
	if err != nil || r.Architecture != runtime.GOARCH {
		return nil, errHostPreflight
	}
	for _, g := range r.Releases {
		resolved, err := filepath.EvalSymlinks(g.Directory)
		if err != nil || resolved != g.Directory || pathInside(g.Directory, c.directory) {
			return nil, errHostPreflight
		}
	}
	o, err := observe(ctx, r)
	if err != nil || o == nil || o.Specs == nil || o.Inventory == nil || o.Execution == nil || len(o.Releases) != 3 || len(o.Installed) != len(r.Installed) || ctx.Err() != nil {
		return nil, errHostPreflight
	}
	intent, err := json.Marshal(struct {
		Version        string `json:"version"`
		SourceRevision string `json:"source_revision"`
		RequestSHA256  string `json:"request_sha256"`
		ArchiveSHA256  string `json:"archive_sha256"`
		CaptureSHA256  string `json:"capture_sha256"`
	}{"jobseek.crawler-host-preflight-intent/v1", c.source, c.expected, o.Specs.ArchiveSHA256(), o.Specs.SHA256()})
	if err != nil || lock.verify() != nil || store.verify() != nil || store.retain("intent.json", intent, hook) != nil {
		return nil, errHostPreflight
	}
	// Intent is fsynced before the first archive publication effect. A retry
	// with spec drift cannot replace this binding or reinterpret the rollback.
	if hook != nil && hook("intent_retained") != nil {
		return nil, errHostPreflight
	}
	if lock.verify() != nil || store.verify() != nil || release.RetainSpecArchive(ctx, filepath.Join(c.directory, "deploy-specs.tar"), o.Specs) != nil {
		return nil, errHostPreflight
	}
	if hook != nil && hook("archive_retained") != nil {
		return nil, errHostPreflight
	}
	installed := []json.RawMessage{}
	for _, f := range o.Installed {
		if f == nil {
			return nil, errHostPreflight
		}
		installed = append(installed, json.RawMessage(f.Body()))
	}
	receipt, err := json.Marshal(struct {
		Version          string                   `json:"version"`
		RuntimeAdmission bool                     `json:"runtime_admission"`
		RequestSHA256    string                   `json:"request_sha256"`
		IntentSHA256     string                   `json:"intent_sha256"`
		Releases         []hostReleaseObservation `json:"requested_releases"`
		Inventory        json.RawMessage          `json:"inventory"`
		Execution        json.RawMessage          `json:"declared_execution"`
		Installed        []json.RawMessage        `json:"installed_container_files"`
		Specs            json.RawMessage          `json:"deploy_specs"`
	}{"jobseek.crawler-host-preflight-observation/v1", false, c.expected, hostDigest(intent), o.Releases, json.RawMessage(o.Inventory.Body()), json.RawMessage(o.Execution.Body()), installed, json.RawMessage(o.Specs.Body())})
	if err != nil || ctx.Err() != nil || lock.verify() != nil || store.verify() != nil {
		return nil, errHostPreflight
	}
	// Immutable content-addressed observations permit a fresh running-state
	// preflight on retry; old inventory bytes are never adopted as current truth.
	if store.retain("preflight-"+hostDigest(receipt)+".json", receipt, hook) != nil {
		return nil, errHostPreflight
	}
	again, err := store.read("request.json", false)
	if err != nil || !bytes.Equal(body, again) || lock.verify() != nil || store.verify() != nil || ctx.Err() != nil {
		return nil, errHostPreflight
	}
	return &HostPreflightResult{"jobseek.crawler-host-preflight-result/v1", "host-preflight", c.source, "preflight_retained", false, c.expected, hostDigest(intent), hostDigest(receipt), receipt}, nil
}
