package main

import (
	"errors"
	"fmt"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"slices"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

const impactAlgorithmVersion = 3
const impactDerivedLimit int64 = 128 * 1024 * 1024

var errImpactValidation = errors.New("inventory impact validation failed")

func impactError(message string) error { return fmt.Errorf("%w: %s", errImpactValidation, message) }

type impactArtifact struct {
	Family, URL, SHA256 string
	Size                int64
	Rows                int
}
type impactBucket struct {
	Kind           string   `json:"kind"`
	Key            string   `json:"key"`
	NameKeys       []string `json:"name_keys"`
	ActiveJobs     int      `json:"active_jobs"`
	RemoteJobs     int      `json:"remote_jobs"`
	LocationHashes []string `json:"location_hashes"`
	CountryCodes   []string `json:"country_codes"`
	LatestPostedAt *string  `json:"latest_posted_at"`
}
type impactFamilySummary struct {
	SchemaVersion    int            `json:"schema_version"`
	AlgorithmVersion int            `json:"algorithm_version"`
	Family           string         `json:"family"`
	ArtifactSHA256   string         `json:"artifact_sha256"`
	ArtifactSize     int64          `json:"artifact_size"`
	ArtifactRows     int            `json:"artifact_rows"`
	DerivedAt        string         `json:"derived_at"`
	BucketCount      int            `json:"bucket_count"`
	KeyedJobs        int            `json:"keyed_jobs"`
	UnkeyedJobs      int            `json:"unkeyed_jobs"`
	Buckets          []impactBucket `json:"buckets"`
}
type mutableImpactBucket struct {
	Kind, Key                   string
	ActiveJobs, RemoteJobs      int
	Names, Locations, Countries map[string]bool
	LatestPostedAt              *string
}

func newMutableImpactBucket(kind, key string) *mutableImpactBucket {
	return &mutableImpactBucket{Kind: kind, Key: key, Names: map[string]bool{}, Locations: map[string]bool{}, Countries: map[string]bool{}}
}
func (b *mutableImpactBucket) add(name, location, country any, remote bool, posted any) {
	b.ActiveJobs++
	if remote {
		b.RemoteJobs++
	}
	if key := normalizeInventoryIdentity(name, 500); key != "" {
		b.Names[key] = true
	}
	if len(b.Locations) < 5000 {
		if key := normalizeInventoryIdentity(location, 1000); key != "" {
			b.Locations[key] = true
		}
	}
	if len(b.Countries) < 250 {
		if key := normalizeInventoryIdentity(country, 2); utf8.RuneCountInString(key) == 2 && inventoryLetters(key) {
			b.Countries[pythonInventoryUpper(key)] = true
		}
	}
	if posted != nil {
		value := strings.TrimSpace(fmt.Sprint(posted))
		if value != "" && utf8.RuneCountInString(value) <= 100 && (b.LatestPostedAt == nil || value > *b.LatestPostedAt) {
			b.LatestPostedAt = inventoryStringPointer(value)
		}
	}
}
func inventoryLetters(s string) bool {
	for _, r := range s {
		if !unicode.IsLetter(r) {
			return false
		}
	}
	return true
}
func pythonInventoryUpper(s string) string { return cases.Upper(language.Und).String(s) }
func sortedInventoryKeys(in map[string]bool) []string {
	out := make([]string, 0, len(in))
	for k := range in {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func (b *mutableImpactBucket) freeze() impactBucket {
	locations := make([]string, 0, len(b.Locations))
	for key := range b.Locations {
		locations = append(locations, sha256Bytes([]byte("location\x00" + key))[:16])
	}
	sort.Strings(locations)
	return impactBucket{Kind: b.Kind, Key: b.Key, NameKeys: sortedInventoryKeys(b.Names), ActiveJobs: b.ActiveJobs, RemoteJobs: b.RemoteJobs, LocationHashes: locations, CountryCodes: sortedInventoryKeys(b.Countries), LatestPostedAt: b.LatestPostedAt}
}
func eligibleInventoryRows(rows []inventoryRow) (map[string][]inventoryRow, error) {
	registry, e := loadCompatibility()
	if e != nil {
		return nil, e
	}
	out := map[string][]inventoryRow{}
	for _, row := range rows {
		if c, ok := registry[row.ATS]; ok && c.candidateEligible() {
			out[row.ATS] = append(out[row.ATS], row)
		}
	}
	return out, nil
}
func uniqueImpactIndex(rows []inventoryRow, key func(inventoryRow) string) map[string]int {
	result := map[string]int{}
	counts := map[string]int{}
	for i, row := range rows {
		v := key(row)
		if v != "" {
			result[v] = i
			counts[v]++
		}
	}
	for k, n := range counts {
		if n != 1 {
			delete(result, k)
		}
	}
	return result
}
func resolveImpactCompanies(familyRows map[string][]inventoryRow, summaries map[string]*impactFamilySummary) ([]companyImpact, map[string]map[string]any) {
	companies := []companyImpact{}
	reports := map[string]map[string]any{}
	families := []string{}
	for f := range familyRows {
		families = append(families, f)
	}
	sort.Strings(families)
	for _, family := range families {
		rows, summary := familyRows[family], summaries[family]
		if summary == nil {
			for _, row := range rows {
				companies = append(companies, companyImpact{ATS: row.ATS, Name: row.Name, Slug: row.Slug, URL: row.URL, ImpactUnknown: true, CountryCodes: []string{}})
			}
			reports[family] = map[string]any{"status": "artifact_missing", "companies": len(rows), "known_companies": 0, "matched_jobs": 0, "unmatched_jobs": 0}
			continue
		}
		tenants := uniqueImpactIndex(rows, func(r inventoryRow) string { return inventoryTenantKey(family, r.URL, nil) })
		slugs := uniqueImpactIndex(rows, func(r inventoryRow) string { return normalizeInventoryIdentity(r.Slug, 500) })
		roots := uniqueImpactIndex(rows, func(r inventoryRow) string { return normalizeInventoryIdentity(strings.SplitN(r.Slug, "/", 2)[0], 500) })
		names := uniqueImpactIndex(rows, func(r inventoryRow) string { return normalizeInventoryIdentity(r.Name, 500) })
		accumulators := make([]*mutableImpactBucket, len(rows))
		for i := range accumulators {
			accumulators[i] = newMutableImpactBucket("", "")
		}
		matched, unmatched := 0, 0
		for _, bucket := range summary.Buckets {
			index, ok := 0, false
			if bucket.Kind == "direct" {
				index, ok = slugs[bucket.Key]
				if !ok {
					index, ok = tenants[bucket.Key]
				}
			} else if bucket.Kind == "tenant" {
				index, ok = tenants[bucket.Key]
			}
			if !ok {
				matches := map[int]bool{}
				for _, name := range bucket.NameKeys {
					for _, lookup := range []map[string]int{names, slugs, roots} {
						if i, found := lookup[name]; found {
							matches[i] = true
						}
					}
				}
				if len(matches) == 1 {
					for i := range matches {
						index = i
						ok = true
					}
				}
			}
			if !ok {
				unmatched += bucket.ActiveJobs
				continue
			}
			a := accumulators[index]
			a.ActiveJobs += bucket.ActiveJobs
			a.RemoteJobs += bucket.RemoteJobs
			for _, v := range bucket.LocationHashes {
				a.Locations[v] = true
			}
			for _, v := range bucket.CountryCodes {
				a.Countries[v] = true
			}
			if bucket.LatestPostedAt != nil && (a.LatestPostedAt == nil || *bucket.LatestPostedAt > *a.LatestPostedAt) {
				a.LatestPostedAt = bucket.LatestPostedAt
			}
			matched += bucket.ActiveJobs
		}
		known := 0
		for i, row := range rows {
			a := accumulators[i]
			unknown := a.ActiveJobs == 0
			if !unknown {
				known++
			}
			companies = append(companies, companyImpact{ATS: row.ATS, Name: row.Name, Slug: row.Slug, URL: row.URL, ImpactUnknown: unknown, ActiveJobs: a.ActiveJobs, RemoteJobs: a.RemoteJobs, LocationCount: len(a.Locations), CountryCodes: sortedInventoryKeys(a.Countries), LatestPostedAt: a.LatestPostedAt})
		}
		reports[family] = map[string]any{"status": "derived", "companies": len(rows), "known_companies": known, "artifact_rows": summary.ArtifactRows, "buckets": len(summary.Buckets), "matched_jobs": matched, "unmatched_jobs": unmatched}
	}
	return companies, reports
}
func impactBucketLess(a, b impactBucket) bool {
	if a.Kind != b.Kind {
		return a.Kind < b.Kind
	}
	if a.Key != b.Key {
		return a.Key < b.Key
	}
	return slices.Compare(a.NameKeys, b.NameKeys) < 0
}
