package worker

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// Spec capture retains files only. The trusted host coordinator must hold the
// shared mutation lock and arm its bound rollback intent before selecting specs.
type ReleaseSpecsConfig struct{ deployment, generation, owner, files, archive, expectedArchive, expectedCapture, source string }
type ReleaseSpecsResult struct {
	Operation             string          `json:"operation"`
	SourceRevision        string          `json:"source_revision"`
	Phase                 string          `json:"phase"`
	FileEvidenceSHA256    string          `json:"file_evidence_sha256"`
	ArchiveSHA256         string          `json:"archive_sha256"`
	CaptureEvidenceSHA256 string          `json:"capture_evidence_sha256"`
	CaptureEvidence       json.RawMessage `json:"capture_evidence"`
}

var errReleaseSpecs = errors.New("ordinary release spec evidence rejected")
var releaseSpecArchivePattern = regexp.MustCompile(`^[a-z0-9][a-z0-9._-]{0,120}\.tar$`)

func pathInside(root, path string) bool {
	relative, err := filepath.Rel(root, path)
	return err != nil || (relative != ".." && !strings.HasPrefix(relative, ".."+string(filepath.Separator)))
}

func ReadReleaseSpecsConfig(getenv func(string) string, source string) (ReleaseSpecsConfig, error) {
	if getenv == nil {
		return ReleaseSpecsConfig{}, errReleaseSpecs
	}
	c := ReleaseSpecsConfig{getenv("ORDINARY_DEPLOY_SPEC_DIRECTORY"), getenv("ORDINARY_RELEASE_GENERATION_DIRECTORY"), getenv("ORDINARY_RELEASE_OWNER"), getenv("ORDINARY_RELEASE_FILES_SHA256"), getenv("ORDINARY_DEPLOY_SPEC_ARCHIVE_PATH"), getenv("ORDINARY_DEPLOY_SPEC_ARCHIVE_SHA256"), getenv("ORDINARY_DEPLOY_SPEC_CAPTURE_SHA256"), source}
	if !filepath.IsAbs(c.deployment) || !filepath.IsAbs(c.generation) || !filepath.IsAbs(c.archive) || pathInside(c.generation, c.archive) || !releaseSpecArchivePattern.MatchString(filepath.Base(c.archive)) || !releaseOwnerPattern.MatchString(c.owner) || !planPattern.MatchString(c.files) || !sourcePattern.MatchString(c.source) || (c.expectedArchive != "" && !planPattern.MatchString(c.expectedArchive)) || (c.expectedCapture != "" && !planPattern.MatchString(c.expectedCapture)) {
		return ReleaseSpecsConfig{}, errReleaseSpecs
	}
	return c, nil
}

func RunReleaseSpecs(ctx context.Context, c ReleaseSpecsConfig) (*ReleaseSpecsResult, error) {
	if ctx == nil || ctx.Err() != nil {
		return nil, errReleaseSpecs
	}
	bounded, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	generation, err := filepath.EvalSymlinks(c.generation)
	parent, parentErr := filepath.EvalSymlinks(filepath.Dir(c.archive))
	if err != nil || parentErr != nil || pathInside(generation, filepath.Join(parent, filepath.Base(c.archive))) {
		return nil, errReleaseSpecs
	}
	s, err := release.CaptureSpecs(bounded, release.SpecCaptureConfig{DeploymentDirectory: c.deployment, GenerationDirectory: c.generation, Owner: c.owner, FileEvidenceSHA256: c.files})
	if err != nil || (c.expectedArchive != "" && c.expectedArchive != s.ArchiveSHA256()) || (c.expectedCapture != "" && c.expectedCapture != s.SHA256()) {
		return nil, errReleaseSpecs
	}
	if release.RetainSpecArchive(bounded, c.archive, s) != nil || bounded.Err() != nil {
		return nil, errReleaseSpecs
	}
	f, err := release.VerifyFiles(bounded, c.generation, c.owner)
	if err != nil || f.SHA256() != c.files || bounded.Err() != nil {
		return nil, errReleaseSpecs
	}
	return &ReleaseSpecsResult{"capture-deploy-specs", c.source, "spec_archive_retained", c.files, s.ArchiveSHA256(), s.SHA256(), json.RawMessage(s.Body())}, nil
}
