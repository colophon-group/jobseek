package dom

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/andybalholm/cascadia"
	"github.com/dlclark/regexp2/v2"
)

var ErrRichRows = errors.New("invalid or incomplete DOM rich-row inventory")

type RichRowBoundary struct{ Selector, Text string }
type RichRowReplacement struct{ Source, Replacement string }
type RichRowsConfig struct {
	RowSelector, LinkSelector, LinkAttr, URLTemplate string
	TitleSelector, TotalSelector, RequiredSelector   string
	DescriptionSelector, DescriptionNextSelector     string
	LocationSelectors, DefaultLocations              []string
	LocationPatterns                                 []*regexp2.Regexp
	LocationMode, LocationSeparator, DuplicatePolicy string
	MetadataSelectors                                map[string]string
	AllowMissingLocations                            bool
	SectionStart, SectionEnd                         *RichRowBoundary
	ActiveURLs, InactiveURLs                         map[string]bool
	TitlePattern, RowPattern                         *regexp2.Regexp
	Replacements                                     []RichRowReplacement
}

// Preserve input order for replacements: successive replacements may overlap.
// Duplicate fields and unknown options cannot create detached configuration.
func richRowsObject(raw []byte) (map[string]json.RawMessage, []string, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	t, e := d.Token()
	if e != nil || t != json.Delim('{') {
		return nil, nil, ErrRichRows
	}
	out := map[string]json.RawMessage{}
	keys := []string{}
	for d.More() {
		t, e = d.Token()
		if e != nil {
			return nil, nil, ErrRichRows
		}
		k, ok := t.(string)
		if !ok || out[k] != nil {
			return nil, nil, ErrRichRows
		}
		var v json.RawMessage
		if d.Decode(&v) != nil {
			return nil, nil, ErrRichRows
		}
		out[k] = v
		keys = append(keys, k)
	}
	if t, e = d.Token(); e != nil || t != json.Delim('}') {
		return nil, nil, ErrRichRows
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return nil, nil, ErrRichRows
	}
	return out, keys, nil
}
func richRowsString(raw json.RawMessage, limit int) (string, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return "", nil
	}
	var v string
	if json.Unmarshal(raw, &v) != nil || v == "" || utf8.RuneCountInString(v) > limit || strings.ContainsRune(v, 0) {
		return "", ErrRichRows
	}
	return v, nil
}
func richRowsSelector(raw json.RawMessage) (string, error) {
	v, e := richRowsString(raw, 256)
	if e != nil {
		return "", e
	}
	if v == "" {
		return "", nil
	}
	v = trim(v)
	if v == "" {
		return "", ErrRichRows
	}
	if _, e = cascadia.Compile(v); e != nil {
		return "", ErrRichRows
	}
	return v, nil
}
func richRowsPattern(raw json.RawMessage, limit int) (*regexp2.Regexp, error) {
	v, e := richRowsString(raw, limit)
	if e != nil {
		return nil, e
	}
	if v == "" {
		return nil, nil
	}
	return CompileURLPattern(v)
}
func richRowsBoundary(raw json.RawMessage) (*RichRowBoundary, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	m, _, e := richRowsObject(raw)
	if e != nil {
		return nil, e
	}
	for k := range m {
		if k != "selector" && k != "text" {
			return nil, ErrRichRows
		}
	}
	selector, e := richRowsSelector(m["selector"])
	if e != nil || selector == "" {
		return nil, ErrRichRows
	}
	text, e := richRowsString(m["text"], 512)
	if e != nil {
		return nil, e
	}
	if len(m["text"]) > 0 && string(m["text"]) != "null" && trim(text) == "" {
		return nil, ErrRichRows
	}
	return &RichRowBoundary{selector, strings.Join(strings.Fields(text), " ")}, nil
}
func richRowsURLs(raw json.RawMessage, allowEmpty bool) (map[string]bool, error) {
	var values []string
	if json.Unmarshal(raw, &values) != nil || values == nil || len(values) > 500 || !allowEmpty && len(values) == 0 {
		return nil, ErrRichRows
	}
	out := map[string]bool{}
	for _, v := range values {
		u, e := url.Parse(v)
		if e != nil || utf8.RuneCountInString(v) > 2048 || strings.ContainsRune(v, 0) || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.RawQuery != "" || u.Fragment != "" || out[v] {
			return nil, ErrRichRows
		}
		out[v] = true
	}
	return out, nil
}

func RichRowsOptions(raw []byte) (*RichRowsConfig, error) {
	if len(raw) == 0 || string(raw) == "null" {
		return nil, nil
	}
	m, _, e := richRowsObject(raw)
	if e != nil {
		return nil, e
	}
	c := &RichRowsConfig{LinkAttr: "href", LocationMode: "all", LocationSeparator: " ", DuplicatePolicy: "error", MetadataSelectors: map[string]string{}}
	allowed := map[string]bool{}
	for _, k := range strings.Fields("row_selector link_selector link_attr url_template title_selector total_selector location_selectors location_selector_mode location_separator location_value_patterns metadata_selectors allow_missing_locations section_start section_end active_urls inactive_urls row_required_selector row_text_pattern description_selector description_next_selector default_locations title_regex title_replacements duplicate_url_policy") {
		allowed[k] = true
	}
	for k := range m {
		if !allowed[k] {
			return nil, ErrRichRows
		}
	}
	for key, target := range map[string]*string{"row_selector": &c.RowSelector, "link_selector": &c.LinkSelector, "title_selector": &c.TitleSelector, "total_selector": &c.TotalSelector, "row_required_selector": &c.RequiredSelector, "description_selector": &c.DescriptionSelector, "description_next_selector": &c.DescriptionNextSelector} {
		*target, e = richRowsSelector(m[key])
		if e != nil {
			return nil, e
		}
	}
	if c.RowSelector == "" || c.DescriptionSelector != "" && c.DescriptionNextSelector != "" {
		return nil, ErrRichRows
	}
	for key, target := range map[string]*string{"link_attr": &c.LinkAttr, "location_selector_mode": &c.LocationMode, "location_separator": &c.LocationSeparator, "duplicate_url_policy": &c.DuplicatePolicy} {
		if v, ok := m[key]; ok {
			if string(v) == "null" || json.Unmarshal(v, target) != nil {
				return nil, ErrRichRows
			}
		}
	}
	if utf8.RuneCountInString(c.LinkAttr) > 64 {
		return nil, ErrRichRows
	}
	c.LinkAttr = trim(c.LinkAttr)
	if !regexp.MustCompile("^[A-Za-z_:][-A-Za-z0-9_:.]*$").MatchString(c.LinkAttr) || len(c.LinkAttr) > 64 || (c.LocationMode != "all" && c.LocationMode != "first") || (c.LocationSeparator != " " && c.LocationSeparator != ";") || (c.DuplicatePolicy != "error" && c.DuplicatePolicy != "prefer_longer_title") {
		return nil, ErrRichRows
	}
	c.URLTemplate, e = richRowsString(m["url_template"], 2048)
	if e != nil {
		return nil, e
	}
	if c.URLTemplate != "" {
		u, e := url.Parse(strings.ReplaceAll(c.URLTemplate, "{value}", "placeholder"))
		if e != nil || strings.Count(c.URLTemplate, "{value}") != 1 || (u.Scheme != "https" && u.Scheme != "http") || u.Host == "" {
			return nil, ErrRichRows
		}
	}
	if v := m["location_selectors"]; len(v) > 0 && string(v) != "null" {
		var selectors []json.RawMessage
		if json.Unmarshal(v, &selectors) != nil || selectors == nil || len(selectors) > 4 {
			return nil, ErrRichRows
		}
		for _, v := range selectors {
			s, e := richRowsSelector(v)
			if e != nil || s == "" {
				return nil, ErrRichRows
			}
			c.LocationSelectors = append(c.LocationSelectors, s)
		}
	}
	if v := m["location_value_patterns"]; len(v) > 0 && string(v) != "null" {
		var patterns []json.RawMessage
		if json.Unmarshal(v, &patterns) != nil || patterns == nil || len(patterns) != len(c.LocationSelectors) {
			return nil, ErrRichRows
		}
		for _, v := range patterns {
			p, e := richRowsPattern(v, 1024)
			if e != nil {
				return nil, e
			}
			c.LocationPatterns = append(c.LocationPatterns, p)
		}
	} else {
		c.LocationPatterns = make([]*regexp2.Regexp, len(c.LocationSelectors))
	}
	if v := m["metadata_selectors"]; len(v) > 0 && string(v) != "null" {
		values, _, e := richRowsObject(v)
		if e != nil || len(values) > 8 {
			return nil, ErrRichRows
		}
		for k, v := range values {
			if !regexp.MustCompile("^[A-Za-z][A-Za-z0-9_.-]{0,63}$").MatchString(k) {
				return nil, ErrRichRows
			}
			s, e := richRowsSelector(v)
			if e != nil || s == "" {
				return nil, ErrRichRows
			}
			c.MetadataSelectors[k] = s
		}
	}
	if v := m["allow_missing_locations"]; len(v) > 0 {
		if json.Unmarshal(v, &c.AllowMissingLocations) != nil || string(v) == "null" {
			return nil, ErrRichRows
		}
	}
	c.SectionStart, e = richRowsBoundary(m["section_start"])
	if e != nil {
		return nil, e
	}
	c.SectionEnd, e = richRowsBoundary(m["section_end"])
	if e != nil || (c.SectionStart == nil) != (c.SectionEnd == nil) {
		return nil, ErrRichRows
	}
	active := len(m["active_urls"]) > 0 && string(m["active_urls"]) != "null"
	inactive := len(m["inactive_urls"]) > 0 && string(m["inactive_urls"]) != "null"
	if active != inactive {
		return nil, ErrRichRows
	}
	if active {
		c.ActiveURLs, e = richRowsURLs(m["active_urls"], false)
		if e != nil {
			return nil, e
		}
		c.InactiveURLs, e = richRowsURLs(m["inactive_urls"], true)
		if e != nil {
			return nil, e
		}
		for v := range c.ActiveURLs {
			if c.InactiveURLs[v] {
				return nil, ErrRichRows
			}
		}
	}
	if v := m["default_locations"]; len(v) > 0 && string(v) != "null" {
		if json.Unmarshal(v, &c.DefaultLocations) != nil || len(c.DefaultLocations) < 1 || len(c.DefaultLocations) > 4 {
			return nil, ErrRichRows
		}
		for i, v := range c.DefaultLocations {
			if trim(v) == "" || utf8.RuneCountInString(v) > 256 || strings.ContainsRune(v, 0) {
				return nil, ErrRichRows
			}
			c.DefaultLocations[i] = trim(v)
		}
	}
	if len(c.DefaultLocations) > 0 && (len(c.LocationSelectors) > 0 || c.AllowMissingLocations) {
		return nil, ErrRichRows
	}
	c.TitlePattern, e = richRowsPattern(m["title_regex"], 2048)
	if e != nil || c.TitlePattern != nil && len(c.TitlePattern.GetGroupNumbers()) != 2 {
		return nil, ErrRichRows
	}
	c.RowPattern, e = richRowsPattern(m["row_text_pattern"], 2048)
	if e != nil {
		return nil, e
	}
	if c.TotalSelector != "" && (c.RequiredSelector != "" || c.RowPattern != nil) {
		return nil, ErrRichRows
	}
	if v, ok := m["title_replacements"]; ok {
		values, keys, e := richRowsObject(v)
		if e != nil || len(keys) > 8 {
			return nil, ErrRichRows
		}
		for _, k := range keys {
			replacement, e := richRowsString(values[k], 128)
			if e != nil || replacement == "" || k == "" || strings.ContainsRune(k, 0) || utf8.RuneCountInString(k) > 128 || k == replacement {
				return nil, ErrRichRows
			}
			c.Replacements = append(c.Replacements, RichRowReplacement{k, replacement})
		}
	}
	return c, nil
}
