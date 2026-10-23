package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/url"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const defaultManifestURL = "https://storage.stapply.ai/jobhive/v1/manifest.json"
const manifestLimit int64 = 2 * 1024 * 1024
const inventoryLimit int64 = 64 * 1024 * 1024

var inventoryFamilyPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_]{0,63}$`)
var inventoryDigestPattern = regexp.MustCompile(`^[0-9a-f]{64}$`)
var errSourceValidation = errors.New("inventory source validation failed")

func sourceError(message string) error { return fmt.Errorf("%w: %s", errSourceValidation, message) }

type manifestDetails struct {
	GeneratedAt     string
	InventoryURL    string
	InventorySHA256 string
	InventorySize   int64
	InventoryRows   int
	FamilyCounts    map[string]int
}

func decodeJSONMap(body []byte) (map[string]any, error) {
	if !utf8.Valid(body) {
		return nil, sourceError("document is not valid UTF8 JSON")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var value map[string]any
	if e := decoder.Decode(&value); e != nil || value == nil {
		return nil, sourceError("document root must be a JSON object")
	}
	var tail any
	if e := decoder.Decode(&tail); e != io.EOF {
		return nil, sourceError("document has trailing JSON")
	}
	return value, nil
}

func parseInventoryTime(value string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05.999999999Z07:00", "2006-01-02T15:04:05.999999999Z0700", "2006-01-02 15:04:05.999999999Z0700", "2006-01-02T15:04Z07:00", "20060102T150405.999999999Z0700"} {
		if t, e := time.Parse(layout, value); e == nil {
			return t.UTC(), nil
		}
	}
	return time.Time{}, sourceError("timestamp is invalid or lacks timezone")
}

func checkedURL(value string) (*url.URL, error) {
	// urllib's public-source readers remove ASCII tab/newline and leading C0.
	text := strings.TrimLeftFunc(value, func(r rune) bool { return r <= 32 })
	text = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(text)
	u, e := url.Parse(text)
	if e != nil {
		return nil, sourceError("invalid URL")
	}
	if port := u.Port(); port != "" {
		n, e := strconv.Atoi(port)
		if e != nil || n < 0 || n > 65535 {
			return nil, sourceError("invalid URL port")
		}
	}
	return u, nil
}

func inventoryHTTPSPort(u *url.URL) bool {
	if u.Port() == "" {
		return true
	}
	n, e := strconv.Atoi(u.Port())
	return e == nil && n == 443
}

func trustedArtifactURL(value string) error {
	u, e := checkedURL(value)
	if e != nil {
		return e
	}
	if strings.ToLower(u.Scheme) != "https" || !strings.EqualFold(u.Hostname(), "storage.stapply.ai") || u.User != nil || u.Fragment != "" || !inventoryHTTPSPort(u) || !strings.HasPrefix(u.EscapedPath(), "/jobhive/v1/") {
		return sourceError("untrusted artifact URL")
	}
	return nil
}

func manifestURL(value map[string]any, key string) (string, error) {
	s, ok := value[key].(string)
	if !ok {
		return "", sourceError("manifest artifact URL is missing")
	}
	if e := trustedArtifactURL(s); e != nil {
		return "", e
	}
	return s, nil
}
func manifestDigest(value map[string]any, key string) (string, error) {
	s, ok := value[key].(string)
	if !ok || !inventoryDigestPattern.MatchString(s) {
		return "", sourceError("invalid manifest artifact checksum")
	}
	return s, nil
}
func manifestInt(value map[string]any, key string, minimum int64) (int64, error) {
	n, ok := value[key].(json.Number)
	if !ok {
		return 0, sourceError("manifest artifact integer is invalid")
	}
	v, e := n.Int64()
	if e != nil || v < minimum {
		return 0, sourceError("manifest artifact integer is invalid")
	}
	return v, nil
}
func stateString(value map[string]any, key string) (string, error) {
	s, ok := value[key].(string)
	if !ok {
		return "", sourceError("current pointer string is invalid")
	}
	return s, nil
}
func stateDigest(value map[string]any, key string) (string, error) {
	s, e := stateString(value, key)
	if e != nil || !inventoryDigestPattern.MatchString(s) {
		return "", sourceError("current pointer checksum is invalid")
	}
	return s, nil
}
func stateFamilyCounts(value map[string]any) (map[string]int, error) {
	raw, ok := value["family_counts"].(map[string]any)
	if !ok {
		return nil, sourceError("current pointer family counts are invalid")
	}
	out := map[string]int{}
	for family, n := range raw {
		if !inventoryFamilyPattern.MatchString(family) {
			return nil, sourceError("current pointer family counts are invalid")
		}
		v, e := manifestInt(map[string]any{"n": n}, "n", 1)
		if e != nil || v > math.MaxInt {
			return nil, sourceError("current pointer family counts are invalid")
		}
		out[family] = int(v)
	}
	return out, nil
}

func validateManifest(m map[string]any) (manifestDetails, error) {
	var out manifestDetails
	if m["version"] != "2.0" {
		return out, sourceError("unsupported manifest version")
	}
	generated, ok := m["generated_at"].(string)
	if !ok {
		return out, sourceError("manifest generated_at is missing")
	}
	if _, e := parseInventoryTime(generated); e != nil {
		return out, e
	}
	companies, ok := m["companies"].(map[string]any)
	if !ok {
		return out, sourceError("manifest company artifacts are missing")
	}
	families, ok := m["by_ats_companies"].(map[string]any)
	if !ok {
		return out, sourceError("manifest family artifacts are missing")
	}
	stats, ok := m["stats"].(map[string]any)
	if !ok {
		return out, sourceError("manifest stats are missing")
	}
	endpoint, e := manifestURL(companies, "csv")
	if e != nil {
		return out, e
	}
	digest, e := manifestDigest(companies, "sha256")
	if e != nil {
		return out, e
	}
	size, e := manifestInt(companies, "size_bytes", 1)
	if e != nil || size > inventoryLimit {
		return out, sourceError("company inventory size is invalid")
	}
	rows, e := manifestInt(companies, "rows", 1)
	if e != nil || rows > math.MaxInt {
		return out, sourceError("company inventory row count is invalid")
	}
	counts := map[string]int{}
	total := int64(0)
	for name, raw := range families {
		if !inventoryFamilyPattern.MatchString(name) {
			return out, sourceError("invalid inventory family")
		}
		a, ok := raw.(map[string]any)
		if !ok {
			return out, sourceError("invalid family artifact")
		}
		if _, e = manifestURL(a, "csv"); e != nil {
			return out, e
		}
		if _, e = manifestDigest(a, "sha256"); e != nil {
			return out, e
		}
		if _, e = manifestInt(a, "size_bytes", 1); e != nil {
			return out, e
		}
		n, e := manifestInt(a, "rows", 1)
		if e != nil || n > math.MaxInt || total > math.MaxInt64-n {
			return out, sourceError("invalid family row count")
		}
		counts[name] = int(n)
		total += n
	}
	if total != rows {
		return out, sourceError("family row counts do not match inventory")
	}
	statsOK := false
	switch v := stats["total_companies"].(type) {
	case json.Number:
		n, e := strconv.ParseFloat(string(v), 64)
		statsOK = e == nil && n == float64(rows)
	case bool:
		statsOK = v && rows == 1
	}
	if !statsOK {
		return out, sourceError("manifest total_companies does not match inventory")
	}
	jobs, ok := m["by_ats"].(map[string]any)
	if !ok {
		return out, sourceError("manifest job artifacts are missing")
	}
	for family, raw := range jobs {
		if !inventoryFamilyPattern.MatchString(family) {
			return out, sourceError("invalid job artifact family")
		}
		a, ok := raw.(map[string]any)
		if !ok {
			return out, sourceError("invalid job artifact")
		}
		if _, e = manifestInt(a, "rows", 0); e != nil {
			return out, e
		}
		for _, keys := range [][3]string{{"csv", "sha256", "size_bytes"}, {"parquet", "parquet_sha256", "parquet_size_bytes"}} {
			if _, ok := a[keys[0]]; !ok {
				continue
			}
			if _, e = manifestURL(a, keys[0]); e != nil {
				return out, e
			}
			if _, e = manifestDigest(a, keys[1]); e != nil {
				return out, e
			}
			if _, e = manifestInt(a, keys[2], 0); e != nil {
				return out, e
			}
		}
	}
	return manifestDetails{GeneratedAt: generated, InventoryURL: endpoint, InventorySHA256: digest, InventorySize: size, InventoryRows: int(rows), FamilyCounts: counts}, nil
}

func pythonSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func cleanInventoryField(value string, max int, trim, optional bool) (string, error) {
	if trim {
		value = strings.TrimFunc(value, pythonSpace)
	}
	if !optional && value == "" || optional && strings.TrimFunc(value, pythonSpace) != value || utf8.RuneCountInString(value) > max {
		return "", sourceError("invalid company inventory field")
	}
	for _, r := range value {
		if r < 32 {
			return "", sourceError("company inventory field contains controls")
		}
	}
	return value, nil
}

func rawURLPath(value string, u *url.URL) string {
	text := strings.TrimLeftFunc(value, func(r rune) bool { return r <= 32 })
	text = strings.NewReplacer("\t", "", "\r", "", "\n", "").Replace(text)
	if i := strings.Index(text, "://"); i >= 0 {
		text = text[i+3:]
		if j := strings.IndexAny(text, "/?#"); j >= 0 {
			text = text[j:]
		} else {
			return ""
		}
		text = strings.SplitN(text, "#", 2)[0]
		text = strings.SplitN(text, "?", 2)[0]
		return text
	}
	return u.Path
}
func normalizedInventoryURL(value string) (string, error) {
	u, e := checkedURL(value)
	if e != nil {
		return "", e
	}
	if strings.ToLower(u.Scheme) != "https" || u.Hostname() == "" || u.User != nil || u.Fragment != "" {
		return "", sourceError("unsafe company URL")
	}
	host := strings.TrimRight(strings.ToLower(u.Hostname()), ".")
	if !inventoryHTTPSPort(u) {
		n, _ := strconv.Atoi(u.Port())
		host += ":" + strconv.Itoa(n)
	}
	path := strings.TrimRight(rawURLPath(value, u), "/")
	if path == "" {
		path = "/"
	}
	result := "https://" + host + path
	if u.RawQuery != "" {
		result += "?" + u.RawQuery
	}
	return result, nil
}

func validateInventory(path string, details manifestDetails) ([]inventoryRow, map[string]int, error) {
	raw, e := boundedInventoryFile(path, inventoryLimit)
	if e != nil || int64(len(raw)) > inventoryLimit || !utf8.Valid(raw) {
		return nil, nil, sourceError("company inventory CSV is unreadable")
	}
	raw = bytes.TrimPrefix(raw, []byte{0xef, 0xbb, 0xbf})
	raw = bytes.ReplaceAll(bytes.ReplaceAll(raw, []byte("\r\n"), []byte("\n")), []byte("\r"), []byte("\n"))
	reader := csv.NewReader(bytes.NewReader(raw))
	reader.FieldsPerRecord = 4
	reader.ReuseRecord = true
	header, e := reader.Read()
	if e != nil || !reflect.DeepEqual(header, []string{"ats", "name", "slug", "url"}) {
		return nil, nil, sourceError("company inventory header must be ats,name,slug,url")
	}
	rows := []inventoryRow{}
	counts := map[string]int{}
	seen := map[string]bool{}
	for {
		record, e := reader.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			return nil, nil, sourceError("company inventory CSV shape is invalid")
		}
		ats, e := cleanInventoryField(record[0], 64, false, false)
		if e != nil || !inventoryFamilyPattern.MatchString(ats) {
			return nil, nil, sourceError("invalid ATS family in inventory")
		}
		name, e := cleanInventoryField(record[1], 500, true, false)
		if e != nil {
			return nil, nil, e
		}
		slug, e := cleanInventoryField(record[2], 500, false, true)
		if e != nil {
			return nil, nil, e
		}
		endpoint, e := cleanInventoryField(record[3], 4096, false, false)
		if e != nil {
			return nil, nil, e
		}
		if _, ok := details.FamilyCounts[ats]; !ok {
			return nil, nil, sourceError("unlisted ATS family in inventory")
		}
		normalized, e := normalizedInventoryURL(endpoint)
		if e != nil {
			return nil, nil, e
		}
		if seen[normalized] {
			return nil, nil, sourceError("duplicate normalized company URL")
		}
		seen[normalized] = true
		counts[ats]++
		rows = append(rows, inventoryRow{ATS: ats, Name: name, Slug: slug, URL: endpoint})
		if len(rows) > details.InventoryRows {
			return nil, nil, sourceError("company inventory has more rows than declared")
		}
	}
	if len(rows) != details.InventoryRows || !reflect.DeepEqual(counts, details.FamilyCounts) {
		return nil, nil, sourceError("company inventory counts do not match manifest")
	}
	return rows, counts, nil
}

func inventoryCoverage(counts map[string]int, minimum float64) (coverage, error) {
	registry, e := loadCompatibility()
	if e != nil {
		return coverage{}, e
	}
	c := coverage{UnsupportedFamilies: []string{}, ExcludedFamilies: []string{}}
	for family, count := range counts {
		c.TotalRows += count
		v, ok := registry[family]
		if !ok {
			c.UnsupportedRows += count
			c.UnsupportedFamilies = append(c.UnsupportedFamilies, family)
		} else if !v.candidateEligible() {
			c.ExcludedRows += count
			c.ExcludedFamilies = append(c.ExcludedFamilies, family)
		}
	}
	c.SupportedRows = c.TotalRows - c.UnsupportedRows - c.ExcludedRows
	c.CandidateRows = c.TotalRows - c.ExcludedRows
	c.SupportedCandidateRows = c.SupportedRows
	rawCandidate, rawClassified := 100.0, 100.0
	if c.CandidateRows > 0 {
		rawCandidate = float64(c.SupportedRows) / float64(c.CandidateRows) * 100
	}
	if c.TotalRows > 0 {
		rawClassified = float64(c.TotalRows-c.UnsupportedRows) / float64(c.TotalRows) * 100
	}
	c.CandidateCoveragePct = math.RoundToEven(rawCandidate*1e6) / 1e6
	c.ClassifiedCoveragePct = math.RoundToEven(rawClassified*1e6) / 1e6
	c.CandidateGenerationAllowed = rawCandidate >= minimum
	sort.Strings(c.UnsupportedFamilies)
	sort.Strings(c.ExcludedFamilies)
	return c, nil
}

func countChangedURLs(previous, current []inventoryRow) int {
	grouped := func(rows []inventoryRow) map[[2]string]map[string]bool {
		out := map[[2]string]map[string]bool{}
		for _, r := range rows {
			identity := "slug:" + r.Slug
			if r.Slug == "" {
				identity = "name:" + unicodeFold(r.Name)
			}
			key := [2]string{r.ATS, identity}
			if out[key] == nil {
				out[key] = map[string]bool{}
			}
			out[key][r.URL] = true
		}
		return out
	}
	old, newer := grouped(previous), grouped(current)
	n := 0
	for key, a := range old {
		if b, ok := newer[key]; ok && !reflect.DeepEqual(a, b) {
			n++
		}
	}
	return n
}
func safeInventoryETag(value string) bool {
	if value == "" || len(value) > 200 {
		return false
	}
	for _, r := range value {
		if r < 32 || r >= 127 {
			return false
		}
	}
	return true
}
func sha256Bytes(body []byte) string {
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:])
}
