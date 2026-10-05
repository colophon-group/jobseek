package oraclehcm

import (
	"encoding/json"
	"errors"
	"math"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

var ErrOptions = errors.New("unsupported Oracle HCM configuration")
var ErrInventory = errors.New("Oracle HCM inventory failed")

var hostPattern = regexp.MustCompile(`^(?:[a-z0-9-]{1,63}\.)+fa\.(?:(?:[a-z]{2}\d+|ocs)\.)?oraclecloud(?:\d{1,3})?\.(?:com|eu)$`)
var sitePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var idPattern = regexp.MustCompile(`^[A-Za-z0-9._-]{1,128}$`)
var facetPattern = regexp.MustCompile(`^[A-Za-z0-9._:-]{1,256}$`)
var languagePattern = regexp.MustCompile(`^[A-Za-z]{2}(?:[-_][A-Za-z]{2})?$`)

var ConfigKeys = []string{"host", "site", "fields", "jobs_count", "organization_id", "offset_overlap", "total_count_tolerance", "page_shortfall_tolerance", "duplicate_row_tolerance"}

type Options struct {
	Host, Site, Organization                                              string
	OffsetOverlap, TotalTolerance, ShortfallTolerance, DuplicateTolerance int
	Fields                                                                map[string]string
}

func integer(value any) (int, bool) {
	var f float64
	switch n := value.(type) {
	case int:
		return n, n >= 0
	case json.Number:
		v, err := strconv.ParseInt(string(n), 10, 32)
		return int(v), err == nil && v >= 0
	case float64:
		f = n
	default:
		return 0, false
	}
	if math.IsNaN(f) || math.IsInf(f, 0) || f < 0 || f > math.MaxInt32 || math.Trunc(f) != f {
		return 0, false
	}
	return int(f), true
}

func facetID(value any) (string, bool) {
	if s, ok := value.(string); ok {
		return s, facetPattern.MatchString(s)
	}
	if n, ok := value.(json.Number); ok {
		s := string(n)
		if len(s) == 0 || len(s) > 128 {
			return "", false
		}
		for _, c := range s {
			if c < '0' || c > '9' {
				return "", false
			}
		}
		return s, true
	}
	if n, ok := integer(value); ok {
		return strconv.Itoa(n), true
	}
	return "", false
}

func normalizeHost(value any) (string, bool) {
	s, ok := value.(string)
	if !ok {
		return "", false
	}
	s = strings.TrimRight(strings.ToLower(strings.TrimSpace(s)), ".")
	return s, hostPattern.MatchString(s)
}

// Candidate identifies public Oracle URLs without trusting arbitrary API hosts.
func Candidate(source string, requireJob bool) (host, site, id string, ok bool) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.RawPath != "" || (u.Port() != "" && u.Port() != "443") {
		return
	}
	host, ok = normalizeHost(u.Hostname())
	if !ok {
		return
	}
	parts := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
	if len(parts) < 5 || parts[0] != "hcmUI" || parts[1] != "CandidateExperience" || !languagePattern.MatchString(parts[2]) || parts[3] != "sites" || !sitePattern.MatchString(parts[4]) {
		ok = false
		return
	}
	site = parts[4]
	tail := parts[5:]
	switch {
	case len(tail) == 0, len(tail) == 1 && (tail[0] == "jobs" || tail[0] == "requisitions"):
	case len(tail) == 2 && tail[0] == "job" && idPattern.MatchString(tail[1]):
		id = tail[1]
	case len(tail) == 3 && tail[0] == "requisitions" && tail[1] == "preview" && idPattern.MatchString(tail[2]):
		id = tail[2]
	default:
		ok = false
	}
	ok = ok && (!requireJob || id != "")
	return
}

func OptionsFromMetadata(boardURL string, m map[string]any) (Options, error) {
	o := Options{Fields: map[string]string{"title": "Title", "locations": "PrimaryLocation", "date_posted": "PostedDate", "employment_type": "JobSchedule"}}
	var ok bool
	if v, exists := m["host"]; exists && v != nil {
		if o.Host, ok = normalizeHost(v); !ok {
			return o, ErrOptions
		}
	}
	if v, exists := m["site"]; exists && v != nil {
		if o.Site, ok = v.(string); !ok || !sitePattern.MatchString(o.Site) {
			return o, ErrOptions
		}
	}
	if o.Host == "" || o.Site == "" {
		host, site, _, valid := Candidate(boardURL, false)
		if !valid {
			return o, ErrOptions
		}
		if o.Host == "" {
			o.Host = host
		}
		if o.Site == "" {
			o.Site = site
		}
	}
	u, err := url.Parse(boardURL)
	if err != nil || u.Scheme != "https" || u.User != nil || u.Hostname() == "" || u.Opaque != "" {
		return o, ErrOptions
	}
	query, err := url.ParseQuery(u.RawQuery)
	if err != nil {
		return o, ErrOptions
	}
	if v, exists := m["organization_id"]; exists && v != nil {
		if o.Organization, ok = facetID(v); !ok {
			return o, ErrOptions
		}
	}
	if values, exists := query["selectedOrganizationsFacet"]; exists {
		if len(values) != 1 || !facetPattern.MatchString(values[0]) || (o.Organization != "" && o.Organization != values[0]) {
			return o, ErrOptions
		}
		o.Organization = values[0]
	}
	for key, dst := range map[string]*int{"offset_overlap": &o.OffsetOverlap, "total_count_tolerance": &o.TotalTolerance, "page_shortfall_tolerance": &o.ShortfallTolerance, "duplicate_row_tolerance": &o.DuplicateTolerance} {
		if value, exists := m[key]; exists {
			n, valid := integer(value)
			if !valid || ((key == "offset_overlap" || key == "page_shortfall_tolerance") && n >= 200) {
				return o, ErrOptions
			}
			*dst = n
		}
	}
	if value, exists := m["fields"]; exists && value != nil {
		fields, valid := value.(map[string]any)
		if !valid {
			return o, ErrOptions
		}
		if len(fields) > 0 {
			// Python's location lookup requires this explicit entry whenever
			// a custom mapping replaces the default. Preserve unsupported
			// partial mappings under their existing owner.
			if _, exists := fields["locations"]; !exists {
				return o, ErrOptions
			}
			for key, value := range fields {
				s, valid := value.(string)
				if !valid || s == "" || len(s) > 256 || strings.ContainsRune(s, 0) || o.Fields[key] == "" {
					return o, ErrOptions
				}
				o.Fields[key] = s
			}
		}
	}
	return o, nil
}

func (o Options) Endpoint() string {
	s := "https://" + o.Host + "/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&expand=requisitionList.workLocation,requisitionList.secondaryLocations&finder=findReqs;siteNumber=" + o.Site + ",facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,limit=200,sortBy=POSTING_DATES_DESC"
	if o.Organization != "" {
		s += ",selectedOrganizationsFacet=" + url.QueryEscape(o.Organization)
	}
	return s
}

func (o Options) JobURL(id string) string {
	return "https://" + o.Host + "/hcmUI/CandidateExperience/en/sites/" + o.Site + "/job/" + id
}
