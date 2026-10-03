package worker

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"regexp"
	"time"

	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
)

// This operation observes files only. Complete host admission also requires
// independent Compose/image observation, all-writer exclusion and readiness.
type ReleaseFilesConfig struct{ directory, owner, expected, source string }
type ReleaseFilesResult struct {
	Operation          string          `json:"operation"`
	SourceRevision     string          `json:"source_revision"`
	Phase              string          `json:"phase"`
	FileEvidenceSHA256 string          `json:"file_evidence_sha256"`
	FileEvidence       json.RawMessage `json:"file_evidence"`
}

var errReleaseFiles = errors.New("ordinary release file evidence rejected")
var releaseOwnerPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

func ReadReleaseFilesConfig(getenv func(string) string, source string) (ReleaseFilesConfig, error) {
	if getenv == nil {
		return ReleaseFilesConfig{}, errReleaseFiles
	}
	c := ReleaseFilesConfig{getenv("ORDINARY_RELEASE_GENERATION_DIRECTORY"), getenv("ORDINARY_RELEASE_OWNER"), getenv("ORDINARY_RELEASE_FILES_SHA256"), source}
	if !filepath.IsAbs(c.directory) || !releaseOwnerPattern.MatchString(c.owner) || !sourcePattern.MatchString(c.source) || (c.expected != "" && !planPattern.MatchString(c.expected)) {
		return ReleaseFilesConfig{}, errReleaseFiles
	}
	return c, nil
}
func RunReleaseFiles(ctx context.Context, c ReleaseFilesConfig) (*ReleaseFilesResult, error) {
	if ctx.Err() != nil {
		return nil, errReleaseFiles
	}
	bounded, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	f, err := release.VerifyFiles(bounded, c.directory, c.owner)
	if err != nil || bounded.Err() != nil || (c.expected != "" && c.expected != f.SHA256()) {
		return nil, errReleaseFiles
	}
	return &ReleaseFilesResult{"verify-release-files", c.source, "files_verified", f.SHA256(), json.RawMessage(f.Body())}, nil
}
