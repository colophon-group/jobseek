package main

import (
	"encoding/json"
	"golang.org/x/text/cases"
	"slices"
	"sort"
)

const atsInventoryLabel = "source:ats-inventory"

type inventoryRow struct {
	ATS  string `json:"ats"`
	Name string `json:"name"`
	Slug string `json:"slug"`
	URL  string `json:"url"`
}

type coverage struct {
	TotalRows                  int      `json:"total_rows"`
	SupportedRows              int      `json:"supported_rows"`
	UnsupportedRows            int      `json:"unsupported_rows"`
	ExcludedRows               int      `json:"excluded_rows"`
	CandidateRows              int      `json:"candidate_rows"`
	SupportedCandidateRows     int      `json:"supported_candidate_rows"`
	ClassifiedCoveragePct      float64  `json:"classified_coverage_pct"`
	CandidateCoveragePct       float64  `json:"candidate_coverage_pct"`
	UnsupportedFamilies        []string `json:"unsupported_families"`
	ExcludedFamilies           []string `json:"excluded_families"`
	CandidateGenerationAllowed bool     `json:"candidate_generation_allowed"`
}

type inventorySnapshot struct {
	ManifestSHA256               string
	ManifestETag                 *string
	InventorySHA256              string
	GeneratedAt                  string
	ValidatedAt                  string
	Manifest                     map[string]any
	Rows                         []inventoryRow
	FamilyCounts                 map[string]int
	Coverage                     coverage
	Changed                      bool
	ETagRevalidated              bool
	NewFamilies, RemovedFamilies []string
	ChangedURLs                  int
}

func nonnilStrings(values []string) []string {
	if values == nil {
		return []string{}
	}
	return values
}

func (s inventorySnapshot) report() map[string]any {
	c := s.Coverage
	c.UnsupportedFamilies = nonnilStrings(c.UnsupportedFamilies)
	c.ExcludedFamilies = nonnilStrings(c.ExcludedFamilies)
	return map[string]any{
		"source": "kalil0321/ats-scrapers published inventory", "data_only": true,
		"manifest_sha256": s.ManifestSHA256, "manifest_etag": s.ManifestETag, "inventory_sha256": s.InventorySHA256,
		"generated_at": s.GeneratedAt, "validated_at": s.ValidatedAt, "changed": s.Changed, "etag_revalidated": s.ETagRevalidated,
		"rows": len(s.Rows), "families": len(s.FamilyCounts), "family_counts": s.FamilyCounts, "new_families": nonnilStrings(s.NewFamilies),
		"removed_families": nonnilStrings(s.RemovedFamilies), "changed_urls": s.ChangedURLs, "coverage": c,
	}
}

type companyImpact struct {
	ATS            string   `json:"ats"`
	Name           string   `json:"name"`
	Slug           string   `json:"slug"`
	URL            string   `json:"url"`
	ImpactUnknown  bool     `json:"impact_unknown"`
	ActiveJobs     int      `json:"active_jobs"`
	RemoteJobs     int      `json:"remote_jobs"`
	LocationCount  int      `json:"location_count"`
	CountryCodes   []string `json:"country_codes"`
	LatestPostedAt *string  `json:"latest_posted_at"`
}

func unicodeFold(s string) string         { return cases.Fold().String(s) }
func (c companyImpact) sourceKey() string { return c.ATS + ":" + unicodeFold(c.URL) }
func (c companyImpact) impactGroup() int {
	if c.ActiveJobs > 0 {
		return 0
	}
	if c.ImpactUnknown {
		return 1
	}
	return 2
}

func impactLess(a, b companyImpact) bool {
	if a.impactGroup() != b.impactGroup() {
		return a.impactGroup() < b.impactGroup()
	}
	if a.ActiveJobs != b.ActiveJobs {
		return a.ActiveJobs > b.ActiveJobs
	}
	if a.LocationCount != b.LocationCount {
		return a.LocationCount > b.LocationCount
	}
	if len(a.CountryCodes) != len(b.CountryCodes) {
		return len(a.CountryCodes) > len(b.CountryCodes)
	}
	if c := slices.Compare(a.CountryCodes, b.CountryCodes); c != 0 {
		return c < 0
	}
	if x, y := unicodeFold(a.Name), unicodeFold(b.Name); x != y {
		return x < y
	}
	return a.sourceKey() < b.sourceKey()
}

type impactSnapshot struct {
	SnapshotSHA256  string
	ManifestSHA256  string
	InventorySHA256 string
	GeneratedAt     string
	DerivedAt       string
	Companies       []companyImpact
	FamilyArtifacts map[string]*string
	FamilyReports   map[string]map[string]any
	Changed         bool
	Downloads       int
}

func (s impactSnapshot) ranked() []companyImpact {
	out := append([]companyImpact{}, s.Companies...)
	sort.SliceStable(out, func(i, j int) bool { return impactLess(out[i], out[j]) })
	return out
}

func (s impactSnapshot) report() map[string]any {
	known, active := 0, 0
	for _, c := range s.Companies {
		if !c.ImpactUnknown {
			known++
		}
		if c.ActiveJobs > 0 {
			active++
		}
	}
	return map[string]any{"snapshot_sha256": s.SnapshotSHA256, "manifest_sha256": s.ManifestSHA256, "inventory_sha256": s.InventorySHA256,
		"generated_at": s.GeneratedAt, "derived_at": s.DerivedAt, "changed": s.Changed, "downloads": s.Downloads, "companies": len(s.Companies),
		"impact_known": known, "impact_unknown": len(s.Companies) - known, "active_companies": active, "family_artifacts": s.FamilyArtifacts, "family_reports": s.FamilyReports}
}

func (c companyImpact) MarshalJSON() ([]byte, error) {
	type plain companyImpact
	c.CountryCodes = nonnilStrings(c.CountryCodes)
	return json.Marshal(plain(c))
}
