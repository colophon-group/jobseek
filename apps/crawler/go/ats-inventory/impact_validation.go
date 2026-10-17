package main

import (
	"encoding/json"
	"math"
	"reflect"
	"regexp"
	"slices"
	"strings"
	"unicode/utf8"
)

var impactLocationHashPattern = regexp.MustCompile(`^[0-9a-f]{16}$`)

func impactString(value map[string]any, key string, max int, allowEmpty bool) (string, error) {
	v, ok := value[key].(string)
	if !ok || !allowEmpty && v == "" || max > 0 && utf8.RuneCountInString(v) > max {
		return "", impactError("cached string is invalid")
	}
	return v, nil
}
func impactInt(value map[string]any, key string) (int, error) {
	n, e := manifestInt(value, key, 0)
	if e != nil || n > math.MaxInt {
		return 0, impactError("cached nonnegative integer is invalid")
	}
	return int(n), nil
}
func impactStrings(value any, maxItems, maxLength int) ([]string, error) {
	raw, ok := value.([]any)
	if !ok || len(raw) > maxItems {
		return nil, impactError("cached string list is invalid")
	}
	out := make([]string, len(raw))
	for i, v := range raw {
		s, ok := v.(string)
		if !ok || s == "" || utf8.RuneCountInString(s) > maxLength || i > 0 && s <= out[i-1] {
			return nil, impactError("cached string list is not bounded, sorted and unique")
		}
		out[i] = s
	}
	return out, nil
}
func impactVersions(m map[string]any) error {
	n, ok := m["algorithm_version"].(json.Number)
	v, e := n.Float64()
	if !sourceSchemaVersion(m["schema_version"]) || !ok || e != nil || v != impactAlgorithmVersion {
		return impactError("unsupported impact cache or algorithm version")
	}
	return nil
}
func decodeImpactBucket(value any) (impactBucket, error) {
	var b impactBucket
	m, ok := value.(map[string]any)
	if !ok {
		return b, impactError("cached bucket must be an object")
	}
	var e error
	b.Kind, _ = m["kind"].(string)
	b.Key, e = impactString(m, "key", 4096, true)
	if e != nil || b.Kind != "direct" && b.Kind != "tenant" && b.Kind != "name" && b.Kind != "unmatched" || (b.Kind == "unmatched") != (b.Key == "") {
		return b, impactError("cached bucket identity is invalid")
	}
	if b.NameKeys, e = impactStrings(m["name_keys"], 1000, 500); e != nil {
		return b, e
	}
	if b.ActiveJobs, e = impactInt(m, "active_jobs"); e != nil {
		return b, e
	}
	if b.RemoteJobs, e = impactInt(m, "remote_jobs"); e != nil {
		return b, e
	}
	if b.RemoteJobs > b.ActiveJobs {
		return b, impactError("remote count exceeds active jobs")
	}
	if b.LocationHashes, e = impactStrings(m["location_hashes"], 5000, 16); e != nil {
		return b, e
	}
	for _, v := range b.LocationHashes {
		if !impactLocationHashPattern.MatchString(v) {
			return b, impactError("cached location identity is invalid")
		}
	}
	if b.CountryCodes, e = impactStrings(m["country_codes"], 250, 2); e != nil {
		return b, e
	}
	for _, v := range b.CountryCodes {
		if utf8.RuneCountInString(v) != 2 || !inventoryLetters(v) || pythonInventoryUpper(v) != v {
			return b, impactError("cached country code is invalid")
		}
	}
	if m["latest_posted_at"] != nil {
		v, e := impactString(m, "latest_posted_at", 100, false)
		if e != nil {
			return b, e
		}
		b.LatestPostedAt = &v
	}
	return b, nil
}
func decodeImpactFamily(body []byte, artifact impactArtifact) (impactFamilySummary, error) {
	var out impactFamilySummary
	m, e := decodeJSONMap(body)
	if e != nil {
		return out, impactError("cached family is not JSON")
	}
	if e = impactVersions(m); e != nil {
		return out, e
	}
	if m["family"] != artifact.Family || m["artifact_sha256"] != artifact.SHA256 {
		return out, impactError("cached family identity differs")
	}
	size, e := manifestInt(m, "artifact_size", 0)
	if e != nil || size != artifact.Size {
		return out, impactError("cached artifact size differs")
	}
	n, e := impactInt(m, "artifact_rows")
	if e != nil || n != artifact.Rows {
		return out, impactError("cached artifact rows differ")
	}
	derived, e := impactString(m, "derived_at", 0, false)
	if e != nil {
		return out, e
	}
	if _, e = parseInventoryTime(derived); e != nil {
		return out, impactError("cached family timestamp is invalid")
	}
	raw, ok := m["buckets"].([]any)
	if !ok || len(raw) > artifact.Rows+1 {
		return out, impactError("cached family buckets invalid")
	}
	buckets := make([]impactBucket, 0, len(raw))
	total, keyed := 0, 0
	for _, v := range raw {
		b, e := decodeImpactBucket(v)
		if e != nil {
			return out, e
		}
		if len(buckets) > 0 && !impactBucketLess(buckets[len(buckets)-1], b) {
			return out, impactError("cached bucket identities are not sorted and unique")
		}
		if b.ActiveJobs > artifact.Rows-total {
			return out, impactError("cached bucket job count exceeds artifact")
		}
		total += b.ActiveJobs
		if b.Kind != "unmatched" {
			keyed += b.ActiveJobs
		}
		buckets = append(buckets, b)
	}
	count, e := impactInt(m, "bucket_count")
	if e != nil || count != len(buckets) || total != artifact.Rows {
		return out, impactError("cached family job counts differ")
	}
	return impactFamilySummary{SchemaVersion: 1, AlgorithmVersion: impactAlgorithmVersion, Family: artifact.Family, ArtifactSHA256: artifact.SHA256, ArtifactSize: artifact.Size, ArtifactRows: artifact.Rows, DerivedAt: derived, BucketCount: len(buckets), KeyedJobs: keyed, UnkeyedJobs: artifact.Rows - keyed, Buckets: buckets}, nil
}
func decodeImpactCompany(value any) (companyImpact, error) {
	var c companyImpact
	m, ok := value.(map[string]any)
	if !ok {
		return c, impactError("cached company is not an object")
	}
	var e error
	if c.ATS, e = impactString(m, "ats", 64, false); e != nil {
		return c, e
	}
	if c.Name, e = impactString(m, "name", 500, false); e != nil {
		return c, e
	}
	if c.Slug, e = impactString(m, "slug", 500, true); e != nil {
		return c, e
	}
	if c.URL, e = impactString(m, "url", 4096, false); e != nil {
		return c, e
	}
	if c.ImpactUnknown, ok = m["impact_unknown"].(bool); !ok {
		return c, impactError("cached company impact_unknown is not boolean")
	}
	if c.ActiveJobs, e = impactInt(m, "active_jobs"); e != nil {
		return c, e
	}
	if c.RemoteJobs, e = impactInt(m, "remote_jobs"); e != nil {
		return c, e
	}
	if c.LocationCount, e = impactInt(m, "location_count"); e != nil {
		return c, e
	}
	if c.RemoteJobs > c.ActiveJobs {
		return c, impactError("cached remote count exceeds active jobs")
	}
	if c.CountryCodes, e = impactStrings(m["country_codes"], 250, 2); e != nil {
		return c, e
	}
	if m["latest_posted_at"] != nil {
		v, e := impactString(m, "latest_posted_at", 100, false)
		if e != nil {
			return c, e
		}
		c.LatestPostedAt = &v
	}
	if c.ImpactUnknown && (c.ActiveJobs != 0 || c.RemoteJobs != 0 || c.LocationCount != 0 || len(c.CountryCodes) != 0 || c.LatestPostedAt != nil) {
		return c, impactError("unknown company has impact counters")
	}
	return c, nil
}
func decodeImpactSnapshot(body []byte, digest string, changed bool, downloads int) (impactSnapshot, error) {
	var out impactSnapshot
	m, e := decodeJSONMap(body)
	if e != nil {
		return out, impactError("cached snapshot is not JSON")
	}
	if e = impactVersions(m); e != nil {
		return out, e
	}
	if out.ManifestSHA256, e = stateDigest(m, "manifest_sha256"); e != nil {
		return out, impactError("cached manifest checksum is invalid")
	}
	if out.InventorySHA256, e = stateDigest(m, "inventory_sha256"); e != nil {
		return out, impactError("cached inventory checksum is invalid")
	}
	if out.GeneratedAt, e = impactString(m, "generated_at", 0, false); e != nil {
		return out, e
	}
	if out.DerivedAt, e = impactString(m, "derived_at", 0, false); e != nil {
		return out, e
	}
	for _, at := range []string{out.GeneratedAt, out.DerivedAt} {
		if _, e = parseInventoryTime(at); e != nil {
			return out, impactError("cached snapshot timestamp is invalid")
		}
	}
	artifacts, ok := m["family_artifacts"].(map[string]any)
	if !ok {
		return out, impactError("cached family artifacts invalid")
	}
	out.FamilyArtifacts = map[string]*string{}
	for family, v := range artifacts {
		if !inventoryFamilyPattern.MatchString(family) {
			return out, impactError("cached family is invalid")
		}
		var ptr *string
		if v != nil {
			s, ok := v.(string)
			if !ok || !inventoryDigestPattern.MatchString(s) {
				return out, impactError("cached artifact checksum is invalid")
			}
			ptr = &s
		}
		out.FamilyArtifacts[family] = ptr
	}
	reports, ok := m["family_reports"].(map[string]any)
	if !ok || len(reports) != len(artifacts) {
		return out, impactError("cached family reports invalid")
	}
	out.FamilyReports = map[string]map[string]any{}
	for family, v := range reports {
		r, ok := v.(map[string]any)
		if _, exists := artifacts[family]; !exists || !ok {
			return out, impactError("cached family report invalid")
		}
		out.FamilyReports[family] = r
	}
	raw, ok := m["companies"].([]any)
	if !ok || len(raw) > 1000000 {
		return out, impactError("cached companies invalid")
	}
	out.Companies = make([]companyImpact, 0, len(raw))
	seen := map[[2]string]bool{}
	for _, v := range raw {
		c, e := decodeImpactCompany(v)
		if e != nil {
			return out, e
		}
		if _, ok := artifacts[c.ATS]; !ok {
			return out, impactError("cached company has unlisted family")
		}
		key := [2]string{c.ATS, c.URL}
		if seen[key] {
			return out, impactError("cached company duplicated")
		}
		seen[key] = true
		out.Companies = append(out.Companies, c)
	}
	out.SnapshotSHA256 = digest
	out.Changed = changed
	out.Downloads = downloads
	return out, nil
}
func sameImpactInputs(previous impactSnapshot, inventory inventorySnapshot, artifacts map[string]*string) bool {
	return previous.ManifestSHA256 == inventory.ManifestSHA256 && previous.InventorySHA256 == inventory.InventorySHA256 && previous.GeneratedAt == inventory.GeneratedAt && reflect.DeepEqual(previous.FamilyArtifacts, artifacts)
}
func sortedImpactFamilies(rows map[string][]inventoryRow) []string {
	out := make([]string, 0, len(rows))
	for f := range rows {
		out = append(out, f)
	}
	slices.Sort(out)
	return out
}
func jobImpactArtifact(manifest map[string]any, family string) (*impactArtifact, error) {
	byATS, ok := manifest["by_ats"].(map[string]any)
	if !ok {
		return nil, impactError("validated manifest lost job artifacts")
	}
	raw := byATS[family]
	if raw == nil {
		return nil, nil
	}
	m, ok := raw.(map[string]any)
	if !ok {
		return nil, impactError("invalid job artifact metadata")
	}
	if m["parquet"] == nil {
		return nil, nil
	}
	endpoint, e := manifestURL(m, "parquet")
	if e != nil {
		return nil, impactError("untrusted Parquet artifact URL")
	}
	digest, e := manifestDigest(m, "parquet_sha256")
	if e != nil {
		return nil, impactError("invalid Parquet checksum")
	}
	size, e := manifestInt(m, "parquet_size_bytes", 1)
	if e != nil {
		return nil, impactError("invalid Parquet size")
	}
	rows, e := impactInt(m, "rows")
	if e != nil {
		return nil, e
	}
	return &impactArtifact{Family: family, URL: strings.Clone(endpoint), SHA256: digest, Size: size, Rows: rows}, nil
}
