package admission

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
)

var ErrInvalidCandidate = errors.New("invalid admission candidate")

const runtimeV1ContractVersion = "crawler.runtime/v1"

// ManifestIdentity is the narrow local boundary for the identity-bearing
// fields of runtime-v1 BoardManifest. It is intentionally not a second wire
// contract. A later runtime-v1 adapter owns full-message validation before it
// constructs this value.
type ManifestIdentity struct {
	ContractVersion   string
	ManifestID        string
	BoardID           string
	CompanyID         string
	ConfigRevision    string
	ConfigFingerprint string
}

// Candidate is an immutable snapshot. Its fields are private and every
// accessor returns a value copy, so injected phases cannot change what was
// admitted or the local digest carried separately by ClaimGrant.
type Candidate struct {
	taskID   string
	manifest ManifestIdentity
	sitemap  sitemap.Config
	digest   [sha256.Size]byte
}

// candidateDigestShape has no maps, interfaces, floats, or optional pointer
// fields. encoding/json therefore gives one stable byte shape for this local
// inactive proof. It is not a runtime-v1 canonicalization rule.
type candidateDigestShape struct {
	TaskID   string                     `json:"task_id"`
	Manifest candidateManifestDigest    `json:"manifest"`
	Sitemap  candidateSitemapConfigHash `json:"sitemap"`
}

type candidateManifestDigest struct {
	ContractVersion   string `json:"contract_version"`
	ManifestID        string `json:"manifest_id"`
	BoardID           string `json:"board_id"`
	CompanyID         string `json:"company_id"`
	ConfigRevision    string `json:"config_revision"`
	ConfigFingerprint string `json:"config_fingerprint"`
}

type candidateSitemapConfigHash struct {
	SitemapURL          string `json:"sitemap_url"`
	IncludeLiteral      string `json:"include_literal"`
	ExcludeLiteral      string `json:"exclude_literal"`
	ReplacePrefix       string `json:"replace_prefix"`
	Replacement         string `json:"replacement"`
	MaxURLs             int    `json:"max_urls"`
	MaxIndexChildren    int    `json:"max_index_children"`
	RootMaxAttempts     int    `json:"root_max_attempts"`
	RootBackoffNanos    int64  `json:"root_backoff_nanos"`
	RequireURLSet       bool   `json:"require_url_set"`
	AllowHTTPForTesting bool   `json:"allow_http_for_testing"`
}

func NewCandidate(taskID string, manifest ManifestIdentity, config sitemap.Config) (Candidate, error) {
	stringsToValidate := []string{
		taskID,
		manifest.ContractVersion,
		manifest.ManifestID,
		manifest.BoardID,
		manifest.CompanyID,
		manifest.ConfigRevision,
		manifest.ConfigFingerprint,
		config.SitemapURL,
		config.IncludeLiteral,
		config.ExcludeLiteral,
		config.ReplacePrefix,
		config.Replacement,
	}
	for _, value := range stringsToValidate {
		if !utf8.ValidString(value) {
			return Candidate{}, ErrInvalidCandidate
		}
	}
	if strings.TrimSpace(taskID) == "" ||
		manifest.ContractVersion != runtimeV1ContractVersion ||
		strings.TrimSpace(manifest.ManifestID) == "" ||
		strings.TrimSpace(manifest.BoardID) == "" ||
		strings.TrimSpace(manifest.CompanyID) == "" ||
		strings.TrimSpace(manifest.ConfigRevision) == "" ||
		strings.TrimSpace(manifest.ConfigFingerprint) == "" ||
		strings.TrimSpace(config.SitemapURL) == "" {
		return Candidate{}, ErrInvalidCandidate
	}
	normalized, err := sitemap.NormalizeConfig(config)
	if err != nil {
		return Candidate{}, ErrInvalidCandidate
	}
	config = normalized

	shape := candidateDigestShape{
		TaskID: taskID,
		Manifest: candidateManifestDigest{
			ContractVersion:   manifest.ContractVersion,
			ManifestID:        manifest.ManifestID,
			BoardID:           manifest.BoardID,
			CompanyID:         manifest.CompanyID,
			ConfigRevision:    manifest.ConfigRevision,
			ConfigFingerprint: manifest.ConfigFingerprint,
		},
		Sitemap: candidateSitemapConfigHash{
			SitemapURL:          config.SitemapURL,
			IncludeLiteral:      config.IncludeLiteral,
			ExcludeLiteral:      config.ExcludeLiteral,
			ReplacePrefix:       config.ReplacePrefix,
			Replacement:         config.Replacement,
			MaxURLs:             config.MaxURLs,
			MaxIndexChildren:    config.MaxIndexChildren,
			RootMaxAttempts:     config.RootMaxAttempts,
			RootBackoffNanos:    int64(config.RootBackoff),
			RequireURLSet:       config.RequireURLSet,
			AllowHTTPForTesting: config.AllowHTTPForTesting,
		},
	}
	encoded, err := json.Marshal(shape)
	if err != nil {
		return Candidate{}, ErrInvalidCandidate
	}
	return Candidate{
		taskID:   taskID,
		manifest: manifest,
		sitemap:  config,
		digest:   sha256.Sum256(encoded),
	}, nil
}

func (candidate Candidate) TaskID() string { return candidate.taskID }

func (candidate Candidate) Manifest() ManifestIdentity { return candidate.manifest }

func (candidate Candidate) Sitemap() sitemap.Config { return candidate.sitemap }

func (candidate Candidate) Digest() [sha256.Size]byte { return candidate.digest }

func (candidate Candidate) valid() bool {
	return candidate.taskID != "" &&
		candidate.manifest.ContractVersion == runtimeV1ContractVersion &&
		candidate.manifest.ManifestID != "" &&
		candidate.manifest.BoardID != "" &&
		candidate.manifest.CompanyID != "" &&
		candidate.manifest.ConfigRevision != "" &&
		candidate.manifest.ConfigFingerprint != "" &&
		candidate.sitemap.SitemapURL != "" &&
		candidate.sitemap.RootBackoff >= 0 &&
		candidate.digest != ([sha256.Size]byte{})
}
