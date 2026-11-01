package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestEvaluateScopeCoverageRequiresContinuousCompleteChecks(t *testing.T) {
	end := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	start := end.Add(-sourceCoverageWindow)
	previous := &coverageCheck{Revision: "same", Started: start.Add(-time.Hour), Finished: start.Add(-time.Hour), NextDue: start.Add(time.Hour), Outcome: "complete"}
	checks := make([]coverageCheck, 0, sourceCoverageWindow/time.Hour+1)
	for at := start.Add(time.Hour); at.Before(end); at = at.Add(time.Hour) {
		checks = append(checks, coverageCheck{Revision: "same", Started: at, Finished: at.Add(time.Minute), NextDue: at.Add(time.Hour + time.Minute), Outcome: "complete"})
	}
	// A source check after the boundary is needed to attest the final edge.
	checks = append(checks, coverageCheck{Revision: "same", Started: end, Finished: end.Add(time.Minute), NextDue: end.Add(time.Hour + time.Minute), Outcome: "complete"})
	assert := func(want string, prev *coverageCheck, cs []coverageCheck, rev string, tracked time.Time, eligible bool) {
		t.Helper()
		got, _ := evaluateScopeCoverage(prev, cs, rev, tracked, start, end, eligible)
		if got != want {
			t.Fatalf("state=%s want %s", got, want)
		}
	}
	assert("complete", previous, checks, "same", start.Add(-time.Hour), true)
	partial := append([]coverageCheck(nil), checks...)
	partial[20].Outcome = "partial"
	assert("interrupted", previous, partial, "same", start.Add(-time.Hour), true)
	failed := append([]coverageCheck(nil), checks...)
	failed[20].Outcome = "failed"
	assert("interrupted", previous, failed, "same", start.Add(-time.Hour), true)
	missed := append([]coverageCheck(nil), checks...)
	missed[20].Started = missed[20].Started.Add(30 * time.Minute)
	assert("interrupted", previous, missed, "same", start.Add(-time.Hour), true)
	assert("unknown", nil, checks, "same", start.Add(-time.Hour), true)
	assert("unknown", previous, checks, "new", start.Add(-time.Hour), true)
	oldScope := *previous
	oldScope.Revision = "old"
	newScopeChecks := []coverageCheck{{Revision: "old", Started: start.Add(time.Hour), Finished: start.Add(time.Hour + time.Minute), NextDue: start.Add(2 * time.Hour), Outcome: "complete"}, {Revision: "new", Started: end, Finished: end.Add(time.Minute), NextDue: end.Add(time.Hour), Outcome: "complete"}}
	assert("unknown", &oldScope, newScopeChecks, "new", start.Add(time.Hour), true)
	assert("unknown", previous, checks, "same", start.Add(time.Second), true)
	assert("unknown", previous, checks, "same", start, true)
	assert("unknown", previous, checks, "same", start.Add(-time.Hour), false)
	assert("unknown", previous, checks[:len(checks)-1], "same", start.Add(-time.Hour), true)
	sparse := *previous
	sparse.NextDue = start.Add(48 * time.Hour)
	assert("unknown", &sparse, checks, "same", start.Add(-time.Hour), true)
}

func TestCoverageArtifactIsByteHashedAndImmutable(t *testing.T) {
	dir := t.TempDir()
	if err := os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	end := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	path := filepath.Join(dir, "2026-11-02.json")
	snapshot := coverageSnapshot{SchemaVersion: 2, SourceVersion: sourceCoverageVersion, SnapshotID: "coverage-test", AsOf: coverageTime(end), GeneratedAt: coverageTime(end.Add(time.Hour)), Companies: []coverageCompany{{ID: "id", Name: "A & Ü", URL: "https://example.com"}}, Scopes: []coverageScope{}, Postings: []coveragePosting{}}
	raw, err := json.Marshal(snapshot)
	if err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(raw)
	wrapper, err := json.Marshal(coverageArtifact{1, base64.StdEncoding.EncodeToString(raw), hex.EncodeToString(sum[:])})
	if err != nil {
		t.Fatal(err)
	}
	if err := publishCoverageArtifact(path, wrapper, end); err != nil {
		t.Fatal(err)
	}
	if err := publishCoverageArtifact(path, wrapper, end); err != nil {
		t.Fatal(err)
	}
	existing, err := readExistingCoverageArtifact(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(existing, wrapper) {
		t.Fatal("published bytes changed")
	}
	if err := validateExistingCoverageArtifact(existing, end); err != nil {
		t.Fatal(err)
	}
	if err := validateExistingCoverageArtifact(make([]byte, sourceCoverageArtifactLimit+1), end); err == nil {
		t.Fatal("oversized coverage artifact accepted")
	}
	badDigest, err := json.Marshal(coverageArtifact{1, base64.StdEncoding.EncodeToString(raw), strings.Repeat("0", 64)})
	if err != nil {
		t.Fatal(err)
	}
	if err := validateExistingCoverageArtifact(badDigest, end); err == nil {
		t.Fatal("invalid byte digest accepted")
	}
	changed := append([]byte(nil), wrapper...)
	changed[len(changed)-2] = 'X'
	if err := publishCoverageArtifact(path, changed, end); err == nil || !strings.Contains(err.Error(), "different immutable") {
		t.Fatalf("changed retry: %v", err)
	}
	if err := os.Symlink(path, filepath.Join(dir, "linked.json")); err != nil {
		t.Fatal(err)
	}
	if _, err := readExistingCoverageArtifact(filepath.Join(dir, "linked.json")); err == nil {
		t.Fatal("followed symlink")
	}
}

func TestCoverageAllowlistAndHomepage(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "companies.txt")
	if err := os.WriteFile(path, []byte("# approved\nacme\nzen\n"), 0600); err != nil {
		t.Fatal(err)
	}
	got, err := readCoverageAllowlist(path)
	if err != nil || len(got) != 2 {
		t.Fatalf("allowlist %v %v", got, err)
	}
	if err := os.WriteFile(path, []byte("acme\nacme\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readCoverageAllowlist(path); err == nil {
		t.Fatal("duplicate company accepted")
	}
	if _, err := canonicalCoverageURL("https://user:pass@example.com/jobs"); err == nil {
		t.Fatal("credential-bearing company URL accepted")
	}
	if _, err := canonicalCoverageURL("http://example.com"); err == nil {
		t.Fatal("insecure company URL accepted")
	}
	root, err := canonicalCoverageURL("https://Example.com")
	if err != nil || root != "https://example.com/" {
		t.Fatalf("noncanonical root homepage: %q %v", root, err)
	}
	homepagePath, err := canonicalCoverageURL("https://example.com/company")
	if err != nil || homepagePath != "https://example.com/company" {
		t.Fatalf("verified non-root homepage was rewritten: %q %v", homepagePath, err)
	}
	defaultPort, err := canonicalCoverageURL("https://Example.com:443/about/")
	if err != nil || defaultPort != "https://example.com/about/" {
		t.Fatalf("default HTTPS port not canonicalized: %q %v", defaultPort, err)
	}
	for _, unsafe := range []string{
		"https://127.0.0.1/", "https://localhost/", "https://service.internal/",
		"https://example.test/", "https://example.com:8080/", "https://example.com/a/../b",
		"https://example.com/a%0Ab", "https://example.com/a\\b",
		"https://example.com/company?ref=x", "https://example.com/company#section",
	} {
		if _, err := canonicalCoverageURL(unsafe); err == nil {
			t.Fatalf("unsafe homepage accepted: %q", unsafe)
		}
	}
	if !validCoverageCompanyName("A & Ü") || validCoverageCompanyName("Hidden<name>") || validCoverageCompanyName("Line\nbreak") {
		t.Fatal("company name safety contract changed")
	}
}

func TestReviewedCareerLinkRegistryRejectsUnsafeOrAmbiguousSources(t *testing.T) {
	parse := func(body string) ([]sourceCoverageLinkInput, error) {
		t.Helper()
		table, err := parseRegistryCSV([]byte("company_slug,board_slug,public_url,provenance\n" + body))
		if err != nil {
			return nil, err
		}
		return prepareSourceCoverageLinks(table)
	}
	links, err := parse("acme,acme-careers,https://Careers.Example.com:443/team/jobs,employer-careers\n")
	if err != nil || len(links) != 1 || links[0].URL != "https://careers.example.com/team/jobs" {
		t.Fatalf("non-root reviewed link was not canonical: %+v %v", links, err)
	}
	for _, raw := range []string{
		"http://example.com/jobs", "https://user:pass@example.com/jobs",
		"https://127.0.0.1/jobs", "https://service.internal/jobs",
		"https://example.test/jobs", "https://careers.example/jobs", "https://example.com:8080/jobs",
		"https://example.com/jobs?token=x", "https://example.com/jobs#fragment",
		"https://example.com/jobs%2Fprivate", "https://example.com/a/../jobs",
		"https://example.com/jobs here",
		"https://example.com/jobs\\private", "https://example.com/jobs\x00secret",
	} {
		if _, err := parse("acme,acme-careers," + raw + ",employer-careers\n"); err == nil {
			t.Fatalf("unsafe public career-page URL accepted: %q", raw)
		}
	}
	for _, body := range []string{
		"acme,acme-careers,https://example.com/jobs,employer-careers\nacme,acme-careers,https://example.com/careers,ats-careers\n",
		"acme,acme-careers,https://example.com/jobs,unreviewed\n",
		"acme,,https://example.com/jobs,employer-careers\n",
	} {
		if _, err := parse(body); err == nil {
			t.Fatalf("invalid public link registry accepted: %q", body)
		}
	}
}
