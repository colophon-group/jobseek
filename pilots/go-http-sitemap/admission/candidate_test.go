package admission

import (
	"errors"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
)

func fixtureManifest(board string) ManifestIdentity {
	return ManifestIdentity{
		ContractVersion:   runtimeV1ContractVersion,
		ManifestID:        "manifest-" + board,
		BoardID:           board,
		CompanyID:         "company-1",
		ConfigRevision:    "revision-7",
		ConfigFingerprint: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
	}
}

func fixtureSitemap(rawURL string) sitemap.Config {
	return sitemap.Config{
		SitemapURL:          rawURL,
		IncludeLiteral:      "/jobs/",
		ExcludeLiteral:      "/archive/",
		ReplacePrefix:       "https://old.example/",
		Replacement:         "https://new.example/",
		MaxURLs:             101,
		MaxIndexChildren:    7,
		RootMaxAttempts:     3,
		RootBackoff:         23 * time.Millisecond,
		RequireURLSet:       true,
		AllowHTTPForTesting: true,
	}
}

func fixtureCandidate(t *testing.T, taskID, boardID, rawURL string) Candidate {
	t.Helper()
	candidate, err := NewCandidate(taskID, fixtureManifest(boardID), fixtureSitemap(rawURL))
	if err != nil {
		t.Fatalf("NewCandidate: %v", err)
	}
	return candidate
}

func TestCandidateSnapshotsManifestAndExactSitemapConfig(t *testing.T) {
	manifest := fixtureManifest("board-1")
	config := fixtureSitemap("https://one.example/sitemap.xml")
	candidate, err := NewCandidate("task-1", manifest, config)
	if err != nil {
		t.Fatal(err)
	}
	wantDigest := candidate.Digest()

	manifest.ManifestID = "mutated"
	manifest.ConfigRevision = "mutated"
	config.SitemapURL = "https://mutated.example/sitemap.xml"
	config.MaxURLs = 1
	returnedManifest := candidate.Manifest()
	returnedManifest.ConfigFingerprint = "mutated"
	returnedConfig := candidate.Sitemap()
	returnedConfig.IncludeLiteral = "mutated"

	if got := candidate.Manifest(); got != fixtureManifest("board-1") {
		t.Fatalf("manifest mutated through caller: %#v", got)
	}
	if got := candidate.Sitemap(); got != fixtureSitemap("https://one.example/sitemap.xml") {
		t.Fatalf("sitemap config mutated through caller: %#v", got)
	}
	if got := candidate.Digest(); got != wantDigest {
		t.Fatal("candidate digest changed after external mutation")
	}
}

func TestCandidateDigestCoversEverySnapshotField(t *testing.T) {
	baseManifest := fixtureManifest("board-1")
	baseConfig := fixtureSitemap("https://one.example/sitemap.xml")
	base, err := NewCandidate("task-1", baseManifest, baseConfig)
	if err != nil {
		t.Fatal(err)
	}

	tests := map[string]func(*string, *ManifestIdentity, *sitemap.Config){
		"task":           func(task *string, _ *ManifestIdentity, _ *sitemap.Config) { *task = "task-2" },
		"manifest id":    func(_ *string, manifest *ManifestIdentity, _ *sitemap.Config) { manifest.ManifestID += "x" },
		"board id":       func(_ *string, manifest *ManifestIdentity, _ *sitemap.Config) { manifest.BoardID += "x" },
		"company id":     func(_ *string, manifest *ManifestIdentity, _ *sitemap.Config) { manifest.CompanyID += "x" },
		"revision":       func(_ *string, manifest *ManifestIdentity, _ *sitemap.Config) { manifest.ConfigRevision += "x" },
		"fingerprint":    func(_ *string, manifest *ManifestIdentity, _ *sitemap.Config) { manifest.ConfigFingerprint += "x" },
		"url":            func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.SitemapURL += "x" },
		"include":        func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.IncludeLiteral += "x" },
		"exclude":        func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.ExcludeLiteral += "x" },
		"replace prefix": func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.ReplacePrefix += "x" },
		"replacement":    func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.Replacement += "x" },
		"max urls":       func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.MaxURLs++ },
		"max children":   func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.MaxIndexChildren++ },
		"attempts":       func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.RootMaxAttempts-- },
		"backoff":        func(_ *string, _ *ManifestIdentity, config *sitemap.Config) { config.RootBackoff++ },
		"require urlset": func(_ *string, _ *ManifestIdentity, config *sitemap.Config) {
			config.RequireURLSet = !config.RequireURLSet
		},
		"allow http": func(_ *string, _ *ManifestIdentity, config *sitemap.Config) {
			config.AllowHTTPForTesting = !config.AllowHTTPForTesting
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			task := "task-1"
			manifest := baseManifest
			config := baseConfig
			mutate(&task, &manifest, &config)
			changed, candidateErr := NewCandidate(task, manifest, config)
			if candidateErr != nil {
				t.Fatalf("NewCandidate: %v", candidateErr)
			}
			if changed.Digest() == base.Digest() {
				t.Fatal("field change did not change digest")
			}
		})
	}
}

func TestFenceEqualityCoversEveryField(t *testing.T) {
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	base := fixtureFence(candidate, "token-1")
	if !base.Equal(base) || !base.validFor(candidate) {
		t.Fatal("valid fence rejected")
	}
	if base.FenceDigest == candidate.Digest() {
		t.Fatal("opaque runtime fence digest was conflated with candidate digest")
	}

	tests := map[string]func(*Fence){
		"task":     func(fence *Fence) { fence.TaskID += "x" },
		"board":    func(fence *Fence) { fence.BoardID += "x" },
		"shard":    func(fence *Fence) { fence.ShardID += "x" },
		"epoch":    func(fence *Fence) { fence.RoutingEpoch++ },
		"owner":    func(fence *Fence) { fence.EngineOwner = "python" },
		"revision": func(fence *Fence) { fence.ConfigRevision += "x" },
		"token":    func(fence *Fence) { fence.ClaimToken += "x" },
		"lease":    func(fence *Fence) { fence.LeaseID += "x" },
		"digest":   func(fence *Fence) { fence.FenceDigest[0]++ },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			changed := base
			mutate(&changed)
			if base.Equal(changed) {
				t.Fatal("changed fence compared equal")
			}
			if changed.validFor(candidate) && (name == "task" || name == "board" || name == "owner" || name == "revision") {
				t.Fatal("candidate identity mismatch was accepted")
			}
		})
	}
}

func TestNewCandidateRejectsIncompleteBoundary(t *testing.T) {
	manifest := fixtureManifest("board-1")
	config := fixtureSitemap("https://one.example/sitemap.xml")
	manifest.ConfigRevision = ""
	if _, err := NewCandidate("task-1", manifest, config); !errors.Is(err, ErrInvalidCandidate) {
		t.Fatalf("got %v", err)
	}
}

func TestNewCandidateRequiresExactRuntimeV1Discriminator(t *testing.T) {
	manifest := fixtureManifest("board-1")
	manifest.ContractVersion = "1.0"
	if _, err := NewCandidate("task-1", manifest, fixtureSitemap("https://one.example/sitemap.xml")); !errors.Is(err, ErrInvalidCandidate) {
		t.Fatalf("got %v", err)
	}
}

func TestNewCandidateRejectsInvalidUTF8BeforeDigesting(t *testing.T) {
	for name, mutate := range map[string]func(*ManifestIdentity, *sitemap.Config){
		"manifest": func(manifest *ManifestIdentity, _ *sitemap.Config) { manifest.ManifestID = "manifest-\xff" },
		"sitemap":  func(_ *ManifestIdentity, config *sitemap.Config) { config.IncludeLiteral = "include-\xfe" },
	} {
		t.Run(name, func(t *testing.T) {
			manifest := fixtureManifest("board-1")
			config := fixtureSitemap("https://one.example/sitemap.xml")
			mutate(&manifest, &config)
			if _, err := NewCandidate("task-1", manifest, config); !errors.Is(err, ErrInvalidCandidate) {
				t.Fatalf("got %v", err)
			}
		})
	}
}

func TestCandidateStoresNormalizedExecutionConfig(t *testing.T) {
	config := fixtureSitemap("https://one.example/sitemap.xml")
	config.RootMaxAttempts = 0
	config.RootBackoff = 0
	candidate, err := NewCandidate("task-1", fixtureManifest("board-1"), config)
	if err != nil {
		t.Fatal(err)
	}
	got := candidate.Sitemap()
	if got.RootMaxAttempts != 3 || got.RootBackoff != 500*time.Millisecond {
		t.Fatalf("defaults were not normalized: %#v", got)
	}
	if got == config {
		t.Fatal("candidate retained unnormalized input")
	}
}

func TestCandidateRejectsEveryInvalidSitemapBoundaryBeforeClaim(t *testing.T) {
	base := fixtureSitemap("https://one.example/sitemap.xml")
	tests := map[string]func(*sitemap.Config){
		"relative": func(config *sitemap.Config) { config.SitemapURL = "/sitemap.xml" },
		"http production": func(config *sitemap.Config) {
			config.SitemapURL = "http://one.example/sitemap.xml"
			config.AllowHTTPForTesting = false
		},
		"userinfo":      func(config *sitemap.Config) { config.SitemapURL = "https://user:secret@one.example/sitemap.xml" },
		"max urls low":  func(config *sitemap.Config) { config.MaxURLs = 0 },
		"max urls high": func(config *sitemap.Config) { config.MaxURLs = 50_001 },
		"children":      func(config *sitemap.Config) { config.MaxIndexChildren = 0 },
		"attempts":      func(config *sitemap.Config) { config.RootMaxAttempts = 4 },
		"backoff":       func(config *sitemap.Config) { config.RootBackoff = time.Duration(1<<62 + 1) },
		"replace pair":  func(config *sitemap.Config) { config.Replacement = "" },
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			config := base
			mutate(&config)
			if _, err := NewCandidate("task-1", fixtureManifest("board-1"), config); !errors.Is(err, ErrInvalidCandidate) {
				t.Fatalf("got %v", err)
			}
		})
	}
}
