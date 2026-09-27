package smartrecruiters

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"
)

const MaxJobs = 50_000
const PageSize = 100
const MaxCanonicalPostings = 500
const ListResponseMaxBytes = 2 << 20
const DetailResponseMaxBytes = 1 << 20

var tokenRE = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var tokenPatterns = []*regexp.Regexp{
	regexp.MustCompile(`api\.smartrecruiters\.com/v1/companies/([\p{L}\p{N}_-]+)`),
	regexp.MustCompile(`jobs\.smartrecruiters\.com/([\p{L}\p{N}_-]+)`),
	regexp.MustCompile(`careers\.smartrecruiters\.com/([\p{L}\p{N}_-]+)`),
}
var uuidRE = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var jobIDRE = regexp.MustCompile(`(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var languageRE = regexp.MustCompile(`^[a-z]{2}$`)
var localizedLanguageRE = regexp.MustCompile(`(?i)^([a-z]{2})(?:[-_][a-z]{2})?$`)
var sourceIdentityRE = regexp.MustCompile(`^[a-z][a-z0-9_-]{1,31}:[a-z0-9][a-z0-9._-]{0,63}:[A-Za-z0-9][A-Za-z0-9._~:/-]{0,383}$`)
var ErrSnapshotChanged = errors.New("publication snapshot changed")
var ErrInactiveDetail = errors.New("publication became inactive")

type Options struct {
	Token     string   `json:"token"`
	Identity  string   `json:"identity"`
	Template  *string  `json:"template"`
	Languages []string `json:"languages"`
}
type Inventory struct {
	URLs       []string `json:"urls"`
	Jobs       []Job    `json:"jobs"`
	Truncated  bool     `json:"truncated"`
	TotalFound int      `json:"total_found"`
}
type GetJSON func(context.Context, string, int) (Object, error)
type Pause func(context.Context, time.Duration) error

func OptionsFromMetadata(boardURL string, meta Object) (Options, error) {
	out := Options{Languages: []string{"en", "de", "fr", "it"}}
	token := meta["token"]
	if !truth(token) {
		for _, pattern := range tokenPatterns {
			match := pattern.FindStringSubmatch(boardURL)
			if len(match) < 2 {
				continue
			}
			candidate := match[1]
			switch candidate {
			case "api", "v1", "js", "css", "assets", "postings", "companies":
				continue
			}
			token = candidate
			break
		}
	}
	out.Token, _ = token.(string)
	if !tokenRE.MatchString(out.Token) {
		return out, errors.New("invalid provider token")
	}
	if value := meta["canonical_identity"]; value != nil {
		out.Identity, _ = value.(string)
		if out.Identity != "job-v1" && out.Identity != "job-location-v1" {
			return out, errors.New("invalid canonical identity mode")
		}
	}
	if value := meta["canonical_job_id_url_template"]; value != nil {
		raw, ok := value.(string)
		if !ok || strings.Count(raw, "{job_id}") != 1 {
			return out, errors.New("invalid canonical URL template")
		}
		if strings.ContainsAny(strings.ReplaceAll(raw, "{job_id}", ""), "{}") {
			return out, errors.New("unsupported placeholder")
		}
		probe, err := url.Parse(strings.ReplaceAll(raw, "{job_id}", "00000000-0000-4000-8000-000000000000"))
		if err != nil || probe.Scheme != "https" || probe.Hostname() == "" || probe.User != nil || probe.RawQuery != "" || probe.Fragment != "" {
			return out, errors.New("invalid canonical HTTPS URL")
		}
		out.Template = &raw
	}
	if out.Identity != "" && out.Template != nil {
		return out, errors.New("conflicting canonical identity modes")
	}
	// Python consults this option only for localized requisition modes.
	if out.Identity == "job-v1" || out.Template != nil {
		if value, exists := meta["language_preference"]; exists {
			values, ok := value.([]any)
			if !ok {
				return out, errors.New("language preference must be a list")
			}
			out.Languages = []string{}
			for _, value := range values {
				s, ok := value.(string)
				s = strings.ToLower(s)
				if !ok || !languageRE.MatchString(s) {
					return out, errors.New("invalid language preference")
				}
				if !contains(out.Languages, s) {
					out.Languages = append(out.Languages, s)
				}
			}
		}
	}
	return out, nil
}
func contains(values []string, value string) bool {
	for _, s := range values {
		if s == value {
			return true
		}
	}
	return false
}
func ListURL(token string) string {
	return "https://api.smartrecruiters.com/v1/companies/" + token + "/postings"
}
func DetailURL(token, id string) string { return ListURL(token) + "/" + id }
func PostingURL(token, id string) string {
	return "https://jobs.smartrecruiters.com/" + token + "/" + id
}
func PublicationID(v any) (string, error) {
	switch v.(type) {
	case string:
	case interface{ String() string }:
		if !regexp.MustCompile(`^-?[0-9]+$`).MatchString(pyString(v)) {
			return "", errors.New("invalid publication id")
		}
	default:
		return "", errors.New("invalid publication id")
	}
	id := trim(pyString(v))
	if id == "" {
		return "", errors.New("empty publication id")
	}
	return id, nil
}
func decimalID(v any) bool {
	s, ok := v.(string)
	if !ok || s == "" || length(s) > 32 {
		return false
	}
	for _, r := range s {
		if !unicode.Is(unicode.Nd, r) {
			return false
		}
	}
	return true
}

func fetchPublicationsOnce(ctx context.Context, opt Options, get GetJSON) ([]Object, bool, int, error) {
	items := []Object{}
	seen := map[string]bool{}
	expected := -1
	canonical := opt.Identity == "job-location-v1"
	for offset := 0; ; offset += PageSize {
		endpoint := fmt.Sprintf("%s?limit=%d&offset=%d", ListURL(opt.Token), PageSize, offset)
		page, err := get(ctx, endpoint, ListResponseMaxBytes)
		if err != nil {
			return nil, false, 0, err
		}
		total, err := integer(page["totalFound"])
		if err != nil || total < 0 || canonical && total > MaxCanonicalPostings {
			return nil, false, 0, errors.New("invalid advertised total")
		}
		if expected != -1 && total != expected {
			if canonical {
				return nil, false, 0, errors.New("canonical advertised total changed")
			}
			return nil, false, 0, ErrSnapshotChanged
		}
		expected = total
		content, ok := page["content"].([]any)
		if !ok {
			return nil, false, 0, errors.New("content must be a list")
		}
		for _, value := range content {
			item, err := requireObject(value, "publication")
			if err != nil {
				return nil, false, 0, err
			}
			id, err := PublicationID(item["id"])
			if err != nil || canonical && !decimalID(item["id"]) {
				return nil, false, 0, errors.New("invalid publication id")
			}
			if seen[id] {
				if canonical {
					return nil, false, 0, errors.New("duplicate canonical publication")
				}
				return nil, false, 0, ErrSnapshotChanged
			}
			seen[id] = true
			items = append(items, item)
		}
		if len(items) >= total {
			if len(items) != total {
				return nil, false, 0, errors.New("inventory exceeds advertised total")
			}
			return items, false, total, nil
		}
		if !canonical && len(items) >= MaxJobs {
			return items, true, total, nil
		}
		if len(content) == 0 || !canonical && len(content) < PageSize {
			return nil, false, 0, errors.New("incomplete inventory")
		}
	}
}
func fetchPublications(ctx context.Context, opt Options, get GetJSON, pause Pause) ([]Object, bool, int, error) {
	for attempt := 0; attempt < 2; attempt++ {
		items, truncated, total, err := fetchPublicationsOnce(ctx, opt, get)
		if !errors.Is(err, ErrSnapshotChanged) || attempt == 1 {
			return items, truncated, total, err
		}
		if err := pause(ctx, time.Second); err != nil {
			return nil, false, 0, err
		}
	}
	panic("unreachable")
}

// Discover buffers one complete coherent snapshot; no partial output is exposed
// on pagination, identity or detail failure. Retries preserve Python's boundary.
func Discover(ctx context.Context, opt Options, get GetJSON, pause Pause) (Inventory, error) {
	empty := Inventory{}
	for attempt := 0; attempt < 2; attempt++ {
		items, truncated, total, err := fetchPublications(ctx, opt, get, pause)
		if err != nil {
			return empty, err
		}
		result := Inventory{URLs: []string{}, Truncated: truncated, TotalFound: total}
		if opt.Identity == "" && opt.Template == nil {
			for _, item := range items {
				id, _ := PublicationID(item["id"])
				result.URLs = append(result.URLs, PostingURL(opt.Token, id))
			}
			sort.Strings(result.URLs)
			return result, nil
		}
		details, err := fetchDetails(ctx, opt, items, get)
		if errors.Is(err, ErrInactiveDetail) && opt.Identity != "job-location-v1" && attempt == 0 {
			if err = pause(ctx, time.Second); err != nil {
				return empty, err
			}
			continue
		}
		if err != nil {
			return empty, err
		}
		result.Jobs, err = ProjectDetails(opt, details)
		if err != nil {
			return empty, err
		}
		for _, job := range result.Jobs {
			result.URLs = append(result.URLs, job.URL)
		}
		sort.Strings(result.URLs)
		return result, nil
	}
	panic("unreachable")
}
