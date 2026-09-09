package canary

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

const testCommit = "0123456789abcdef0123456789abcdef01234567"

func TestProductionManifestIsStrictAndBounded(t *testing.T) {
	manifest, digest, err := LoadManifest("production.json")
	if err != nil {
		t.Fatal(err)
	}
	if len(manifest.Jobs) != 2 {
		t.Fatalf("jobs = %d, want 2", len(manifest.Jobs))
	}
	if len(digest) != 64 {
		t.Fatalf("digest length = %d, want 64", len(digest))
	}
	for _, job := range manifest.Jobs {
		if job.MaxURLs != maxProtocolURLs {
			t.Fatalf("%s max_urls = %d", job.ID, job.MaxURLs)
		}
	}
}

func TestDecodeManifestRejectsUnknownAndModifiedInputs(t *testing.T) {
	tests := []string{
		`{"schema_version":1,"jobs":[],"extra":true}`,
		`{"schema_version":1,"jobs":[{"id":"snap-fusion-atlantic-recruitee","sitemap_url":"https://example.com/sitemap.xml","include_literal":"fusion-energy-venture","exclude_literal":"","max_urls":50000,"max_index_children":16}]}`,
		`{"schema_version":1,"jobs":[{"id":"snap-fusion-atlantic-recruitee","sitemap_url":"https://jobsatlanticvcfoodlabs.recruitee.com/sitemap.xml","include_literal":"fusion-energy-venture","exclude_literal":"","max_urls":50000,"max_index_children":16},{"id":"snap-fusion-atlantic-recruitee","sitemap_url":"https://jobsatlanticvcfoodlabs.recruitee.com/sitemap.xml","include_literal":"fusion-energy-venture","exclude_literal":"","max_urls":50000,"max_index_children":16}]}`,
		`{"schema_version":1,"jobs":[{"id":"unknown","sitemap_url":"https://unknown.invalid/sitemap.xml","include_literal":"","exclude_literal":"","max_urls":1,"max_index_children":1}]}`,
		`{"schema_version":1,"jobs":[]} {}`,
	}
	for _, input := range tests {
		if _, err := DecodeManifest([]byte(input)); err == nil {
			t.Fatalf("DecodeManifest(%q) unexpectedly succeeded", input)
		}
	}
}

func TestLoadManifestRejectsOversizedFileBeforeDecoding(t *testing.T) {
	path := filepath.Join(t.TempDir(), "manifest.json")
	if err := os.WriteFile(path, []byte(strings.Repeat("x", MaxManifestBytes+1)), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, err := LoadManifest(path); err == nil {
		t.Fatal("oversized manifest unexpectedly succeeded")
	}
}

func TestCanonicalURLSHA256IsOrderIndependentAndUnambiguous(t *testing.T) {
	left := canonicalURLSHA256([]string{"https://example.test/a", "https://example.test/b"})
	right := canonicalURLSHA256([]string{"https://example.test/b", "https://example.test/a"})
	if left != right {
		t.Fatalf("order changed digest: %s != %s", left, right)
	}
	if left == canonicalURLSHA256([]string{"https://example.test/ahttps://example.test/b"}) {
		t.Fatal("length framing did not distinguish inputs")
	}
}

func TestJobReportNeverSerializesDiscoveredURLsOrRawError(t *testing.T) {
	result := worker.Result{
		JobID: "snap-fusion-atlantic-recruitee",
		Sitemap: sitemap.Result{
			URLs: []string{"https://secret.example/credential?token=do-not-emit"},
		},
	}
	report := makeJobReport(result)
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "secret.example") || strings.Contains(string(encoded), "do-not-emit") {
		t.Fatalf("report leaked URL: %s", encoded)
	}
	if report.CanonicalURLCount != 1 || report.CanonicalURLSHA256 == "" {
		t.Fatalf("unexpected count/hash report: %+v", report)
	}
}

func TestTruncationFailsJobReport(t *testing.T) {
	report := makeJobReport(worker.Result{
		JobID: "verity-breezy",
		Sitemap: sitemap.Result{
			URLs:      []string{"https://verity-ag.breezy.hr/p/example"},
			Truncated: true,
		},
	})
	if report.Status != "failed" || report.ErrorKind != "truncated" {
		t.Fatalf("truncated report = %+v", report)
	}
}

func TestEmptyResultFailsJobReport(t *testing.T) {
	report := makeJobReport(worker.Result{JobID: "verity-breezy"})
	if report.Status != "failed" || report.ErrorKind != "empty_result" {
		t.Fatalf("empty report = %+v", report)
	}
}

func TestSourceIdentityMustMatchImmutableSHATag(t *testing.T) {
	identity := "ghcr.io/colophon-group/jobseek-go-sitemap-shadow:sha-" + testCommit
	if !ValidSourceIdentity(testCommit, identity) {
		t.Fatal("valid source identity rejected")
	}
	if ValidSourceIdentity(testCommit, identity+"-mutable") {
		t.Fatal("mutable suffix accepted")
	}
	if ValidSourceIdentity(strings.ToUpper(testCommit), identity) {
		t.Fatal("uppercase commit accepted")
	}
}

func TestTypedErrorsTakePrecedenceOverWrappedContext(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want string
	}{
		{
			name: "bounded HTTP timeout",
			err:  &boundedhttp.Error{Kind: boundedhttp.ErrorTimeout, Err: context.DeadlineExceeded},
			want: "http_timeout",
		},
		{
			name: "sitemap deadline",
			err:  &sitemap.Error{Kind: sitemap.ErrorDeadline, Err: context.DeadlineExceeded},
			want: "sitemap_deadline",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := typedErrorKind(test.err); got != test.want {
				t.Fatalf("typedErrorKind() = %q, want %q", got, test.want)
			}
		})
	}
}
