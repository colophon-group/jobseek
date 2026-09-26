package workable

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const MaxJobs = 50_000

var slugRE = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$`)
var shortcodeRE = regexp.MustCompile(`^[a-zA-Z0-9_-]+$`)
var openingsRE = regexp.MustCompile(`All open roles .*?:\s*([\d,]+) current openings?\b`)

type Inventory struct {
	URLs          []string `json:"urls"`
	Truncated     bool     `json:"truncated"`
	VerifiedEmpty bool     `json:"verified_empty"`
}

func validSlug(slug string) bool { return slugRE.MatchString(slug) }

func jobURL(slug, shortcode string) string {
	return "https://apply.workable.com/" + slug + "/j/" + shortcode + "/"
}

func truthy(raw json.RawMessage) bool {
	trimmed := bytes.TrimSpace(raw)
	return len(trimmed) > 0 && !bytes.Equal(trimmed, []byte("null")) && !bytes.Equal(trimmed, []byte("false")) && !bytes.Equal(trimmed, []byte(`""`)) && !bytes.Equal(trimmed, []byte("0")) && !bytes.Equal(trimmed, []byte("[]")) && !bytes.Equal(trimmed, []byte("{}"))
}

// ParsePage returns canonical URLs and the provider's opaque nextPage token.
// It preserves the Python monitor's default of an empty results list when the
// key is absent, while failing on malformed list items instead of accepting a
// partial inventory.
func ParsePage(slug string, body []byte) ([]string, json.RawMessage, bool, error) {
	if !validSlug(slug) {
		return nil, nil, false, errors.New("Workable slug is invalid")
	}
	var page map[string]json.RawMessage
	if err := json.Unmarshal(body, &page); err != nil || page == nil {
		return nil, nil, false, errors.New("Workable list response is not a JSON object")
	}
	var rows []map[string]json.RawMessage
	if raw, ok := page["results"]; ok {
		if err := json.Unmarshal(raw, &rows); err != nil || rows == nil {
			return nil, nil, false, errors.New("Workable results is not a list of objects")
		}
	}
	urls := make([]string, 0, len(rows))
	for _, row := range rows {
		if row == nil {
			return nil, nil, false, errors.New("Workable result is not an object")
		}
		raw := row["shortcode"]
		if !truthy(raw) {
			continue
		}
		var shortcode string
		if err := json.Unmarshal(raw, &shortcode); err != nil || !shortcodeRE.MatchString(shortcode) {
			return nil, nil, false, errors.New("Workable shortcode is invalid")
		}
		urls = append(urls, jobURL(slug, shortcode))
	}
	next := page["nextPage"]
	return urls, next, truthy(next) && len(rows) > 0, nil
}

func parseAdvertisedCount(body []byte) (int, error) {
	match := openingsRE.FindSubmatch(body)
	if match == nil {
		return 0, errors.New("Workable llms.txt did not advertise a current-opening count")
	}
	count, err := strconv.Atoi(strings.ReplaceAll(string(match[1]), ",", ""))
	if err != nil || count < 0 || count > MaxJobs {
		return 0, errors.New("Workable advertised count is invalid or exceeds the monitor cap")
	}
	return count, nil
}

func parseMarkdownURLs(slug string, body []byte) []string {
	pattern := regexp.MustCompile(`https://apply\.workable\.com/` + regexp.QuoteMeta(slug) + `/jobs/view/([\w-]+)\.md\b`)
	seen := map[string]struct{}{}
	for _, match := range pattern.FindAllSubmatch(body, -1) {
		seen[jobURL(slug, string(match[1]))] = struct{}{}
	}
	return sortedURLs(seen)
}

func parsePublicAPIURLs(slug string, body []byte) ([]string, error) {
	var envelope struct {
		Jobs []map[string]json.RawMessage `json:"jobs"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil || envelope.Jobs == nil {
		return nil, errors.New("Workable public jobs API did not return a jobs list")
	}
	seen := map[string]struct{}{}
	for _, row := range envelope.Jobs {
		if row == nil {
			return nil, errors.New("Workable public API returned a non-object job")
		}
		var shortcode string
		if err := json.Unmarshal(row["shortcode"], &shortcode); err != nil || !shortcodeRE.MatchString(shortcode) {
			return nil, errors.New("Workable public API returned a job without a shortcode")
		}
		seen[jobURL(slug, shortcode)] = struct{}{}
	}
	return sortedURLs(seen), nil
}

func sortedURLs(seen map[string]struct{}) []string {
	urls := make([]string, 0, len(seen))
	for url := range seen {
		urls = append(urls, url)
	}
	sort.Strings(urls)
	return urls
}

func checkAdvertised(urls []string, advertised int, source string) error {
	if len(urls) != advertised {
		return fmt.Errorf("Workable %s inventory count mismatch: advertised %d, found %d unique jobs", source, advertised, len(urls))
	}
	return nil
}

// ProjectFallback replays exact bodies captured from an existing Python
// Markdown fallback. It performs no origin request.
func ProjectFallback(slug string, llms, jobs, public []byte) (Inventory, error) {
	if !validSlug(slug) {
		return Inventory{}, errors.New("Workable slug is invalid")
	}
	advertised, err := parseAdvertisedCount(llms)
	if err != nil {
		return Inventory{}, err
	}
	if advertised == 0 {
		return Inventory{URLs: []string{}, VerifiedEmpty: true}, nil
	}
	urls := parseMarkdownURLs(slug, jobs)
	if len(urls) == 0 && bytes.Contains(jobs, []byte("Use the search endpoint to filter results")) {
		if len(public) == 0 {
			return Inventory{}, errors.New("Workable public fallback body is missing")
		}
		urls, err = parsePublicAPIURLs(slug, public)
		if err != nil {
			return Inventory{}, err
		}
	}
	if err := checkAdvertised(urls, advertised, "fallback"); err != nil {
		return Inventory{}, err
	}
	return Inventory{URLs: urls}, nil
}
