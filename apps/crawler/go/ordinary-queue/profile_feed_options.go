package queue

import (
	"encoding/json"
	"errors"
	"regexp"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
)

// A configured scraper does not imply enrichment for a rich feed. The legacy
// processor uses the feed's complete content unless enrich is explicitly set.
func feedRichDetailAssignment(config map[string]string) error {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return err
	}
	var scraper string
	if json.Unmarshal(md["scraper_type"], &scraper) != nil || (scraper != "skip" && scraper != "json-ld" && scraper != "dom" && scraper != "embedded") {
		return ErrUnsupportedProfile
	}
	fields, err := monitorEnrichmentFields(config, map[string]bool{})
	if err != nil || len(fields) != 0 {
		return ErrUnsupportedProfile
	}
	return nil
}

func feedHasDetailAssignment(md map[string]json.RawMessage) bool {
	var scraper string
	return json.Unmarshal(md["scraper_type"], &scraper) == nil && scraper != "skip"
}

// Feed policy changes inventory/content selection and identities, never fetch
// authority. Provider-boundary rejections require a failed terminal cycle.
type FeedURLRules struct {
	include, exclude       *regexp2.Regexp
	allowlist              *regexp2.Regexp
	JobFilter              *FeedJobFilter
	find                   *regexp.Regexp
	replacement            string
	postDiscoveryURLFilter bool
}

func FeedMonitorURLRules(config map[string]string) (*FeedURLRules, error) {
	md, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return nil, err
	}
	r := &FeedURLRules{}
	r.JobFilter, err = feedJobFilter(md["job_filter"])
	if err != nil {
		return nil, err
	}
	if raw, present := md["url_allowlist"]; present {
		var pattern string
		if json.Unmarshal(raw, &pattern) != nil || pattern == "" || len(pattern) > 2048 {
			return nil, ErrUnsupportedProfile
		}
		r.allowlist, err = dom.CompileURLPattern(`\A(?:` + pattern + `)\Z`)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
	}
	var include, exclude string
	if raw, ok := md["url_filter"]; ok && string(raw) != "null" {
		if json.Unmarshal(raw, &include) != nil {
			f, err := profileMetadataFields(string(raw), map[string]bool{"include": true, "exclude": true})
			if err != nil {
				return nil, err
			}
			for key, value := range f {
				if string(value) == "null" {
					continue
				}
				destination := &include
				if key == "exclude" {
					destination = &exclude
				}
				if json.Unmarshal(value, destination) != nil {
					return nil, ErrUnsupportedProfile
				}
			}
		}
	}
	r.postDiscoveryURLFilter = include != "" || exclude != ""
	if r.include, err = dom.CompileURLPattern(include); err != nil {
		return nil, ErrUnsupportedProfile
	}
	if exclude != "" {
		if r.exclude, err = dom.CompileURLPattern(exclude); err != nil {
			return nil, ErrUnsupportedProfile
		}
	}
	if raw, ok := md["url_transform"]; ok && string(raw) != "null" {
		f, err := profileMetadataFields(string(raw), map[string]bool{"find": true, "replace": true})
		if err != nil {
			return nil, err
		}
		var find, replacement string
		if json.Unmarshal(f["find"], &find) != nil || len(find) > 2048 || json.Unmarshal(f["replace"], &replacement) != nil || len(replacement) > 4096 || strings.Contains(replacement, "{identity}") {
			return nil, ErrUnsupportedProfile
		}
		if find == "" {
			return r, nil
		}
		r.find, err = regexp.Compile(find)
		if err != nil {
			return nil, ErrUnsupportedProfile
		}
		var out strings.Builder
		for n := 0; n < len(replacement); n++ {
			switch replacement[n] {
			case '$':
				out.WriteString("$$")
			case '\\':
				n++
				if n >= len(replacement) || replacement[n] < '1' || replacement[n] > '9' || int(replacement[n]-'0') > r.find.NumSubexp() || n+1 < len(replacement) && replacement[n+1] >= '0' && replacement[n+1] <= '9' {
					return nil, ErrUnsupportedProfile
				}
				out.WriteString("${")
				out.WriteByte(replacement[n])
				out.WriteByte('}')
			default:
				out.WriteByte(replacement[n])
			}
		}
		r.replacement = out.String()
	}
	return r, nil
}

var ErrProviderBoundary = errors.New("provider boundary allowlist rejected inventory")
var ErrProviderClassification = errors.New("provider job classification is ambiguous")

func (r *FeedURLRules) HasURLFilter() bool { return r != nil && r.postDiscoveryURLFilter }

func (r *FeedURLRules) RequiresRawInventory() bool {
	return r != nil && (r.allowlist != nil || r.JobFilter != nil)
}

func (r *FeedURLRules) ProviderAllows(source string) (bool, error) {
	if r == nil {
		return false, ErrConfiguration
	}
	if r.allowlist == nil {
		return true, nil
	}
	return r.allowlist.MatchString(source)
}

func (r *FeedURLRules) Rewrite(source string) string {
	if r.find != nil {
		return r.find.ReplaceAllString(source, r.replacement)
	}
	return source
}

func (r *FeedURLRules) Apply(source string) (string, bool, error) {
	keep, err := r.FilterURL(source)
	if err != nil || !keep {
		return "", keep, err
	}
	return r.Rewrite(source), true, nil
}

func (r *FeedURLRules) FilterURL(source string) (bool, error) {
	if r == nil || len(source) > 8192 || strings.ContainsAny(source, "\x00\r\n") {
		return false, ErrUnsupportedProfile
	}
	keep, err := r.include.MatchString(source)
	if err != nil || !keep {
		return false, err
	}
	if r.exclude != nil {
		reject, err := r.exclude.MatchString(source)
		if err != nil || reject {
			return false, err
		}
	}
	return true, nil
}
