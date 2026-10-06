package apisniffer

import (
	"encoding/json"
	"io"
	"regexp"
	"strings"

	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
)

type inlineDeadlinePattern struct {
	re     *regexp2.Regexp
	format string
}
type InlineOptions struct {
	Steps                                                                                                        []dom.Object
	Item, Start, End                                                                                             dom.Object
	Defaults, DefaultsByTitle                                                                                    map[string]any
	ExcludeTitles                                                                                                map[string]bool
	ExcludeTitle, ExcludeDescription                                                                             *regexp2.Regexp
	Deadlines                                                                                                    []inlineDeadlinePattern
	DateFormat, StableField                                                                                      string
	EmptySelector, EmptyText, NonemptySelector                                                                   string
	SourceSelector, SourceAttribute, SourceURLSelector, SourceURLAttribute                                       string
	SourcePattern                                                                                                *regexp2.Regexp
	IncludeHidden, PreserveLocation, DescriptionFromTitle, ExcludeExpired, RequireZeroProof, EmptyRequiresNoJobs bool
	Positions                                                                                                    int
}

var inlineFieldName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_.-]*$`)
var inlineAttributeName = regexp.MustCompile(`^[A-Za-z_:][A-Za-z0-9:._-]*$`)

func inlineBool(m map[string]any, key string) (bool, error) {
	if v, exists := m[key]; exists {
		b, ok := v.(bool)
		if !ok {
			return false, ErrOptions
		}
		return b, nil
	}
	return false, nil
}
func inlineOptionalText(m map[string]any, key string, max int) (string, error) {
	if m[key] == nil {
		return "", nil
	}
	v, ok := m[key].(string)
	if !ok || v == "" || len([]rune(v)) > max || strings.ContainsRune(v, 0) {
		return "", ErrOptions
	}
	return v, nil
}
func inlineSelector(m map[string]any, key string) (string, error) {
	s, err := inlineOptionalText(m, key, 256)
	if err != nil {
		return "", err
	}
	if s == "" {
		return "", nil
	}
	s = inlineTrim(s)
	if s == "" {
		return "", ErrOptions
	}
	if _, err = cascadia.Parse(s); err != nil {
		return "", ErrOptions
	}
	return s, nil
}
func inlinePattern(m map[string]any, key, prefix string, captures int) (*regexp2.Regexp, error) {
	s, err := inlineOptionalText(m, key, 2048)
	if err != nil || s == "" {
		return nil, err
	}
	if key == "source_identity_regex" {
		s = `\A(?:` + s + `)\z`
	}
	re, err := dom.CompileURLPattern(prefix + s)
	if err != nil {
		return nil, ErrOptions
	}
	groups := len(re.GetGroupNumbers()) - 1
	if captures > 0 && groups != captures || captures < 0 && groups < 1 {
		return nil, ErrOptions
	}
	return re, nil
}
func inlineObject(m map[string]any, key string) (map[string]any, error) {
	if m[key] == nil || !detailTruthy(m[key]) {
		return map[string]any{}, nil
	}
	v, ok := m[key].(map[string]any)
	if !ok {
		return nil, ErrOptions
	}
	return v, nil
}
func InlineDocumentOptions(m map[string]any) (InlineOptions, error) {
	o := InlineOptions{Positions: 1, ExcludeTitles: map[string]bool{}}
	var err error
	for key, dest := range map[string]*bool{"include_hidden": &o.IncludeHidden, "preserve_single_location": &o.PreserveLocation, "description_from_title": &o.DescriptionFromTitle, "exclude_expired": &o.ExcludeExpired, "require_zero_proof": &o.RequireZeroProof, "empty_requires_no_jobs": &o.EmptyRequiresNoJobs} {
		*dest, err = inlineBool(m, key)
		if err != nil {
			return o, err
		}
	}
	if m["positions_per_listing"] != nil {
		n, ok := integer(m["positions_per_listing"])
		if !ok || n < 1 || n > 20 {
			return o, ErrOptions
		}
		o.Positions = n
	}
	for key, dest := range map[string]*string{"synthetic_identity_field": &o.StableField, "valid_through_format": &o.DateFormat} {
		*dest, err = inlineOptionalText(m, key, 128)
		if err != nil {
			return o, err
		}
	}
	if o.StableField != "" && (!inlineFieldName.MatchString(o.StableField) || o.Positions != 1) {
		return o, ErrOptions
	}
	o.Defaults, err = inlineObject(m, "defaults")
	if err != nil {
		return o, err
	}
	o.DefaultsByTitle, err = inlineObject(m, "defaults_by_title")
	if err != nil {
		return o, err
	}
	if detailTruthy(m["exclude_titles"]) {
		a, ok := m["exclude_titles"].([]any)
		if !ok {
			return o, ErrOptions
		}
		for _, v := range a {
			s, ok := v.(string)
			if !ok {
				return o, ErrOptions
			}
			o.ExcludeTitles[s] = true
		}
	}
	o.ExcludeTitle, err = inlinePattern(m, "exclude_title_regex", "", 0)
	if err != nil {
		return o, err
	}
	o.ExcludeDescription, err = inlinePattern(m, "exclude_description_regex", "", 0)
	if err != nil {
		return o, err
	}
	o.EmptyText, err = inlineOptionalText(m, "empty_text", 512)
	if err != nil {
		return o, err
	}
	if o.EmptyText != "" {
		o.EmptyText = inlineNormalized(o.EmptyText)
		if o.EmptyText == "" {
			return o, ErrOptions
		}
	}
	for key, dest := range map[string]*string{"empty_selector": &o.EmptySelector, "nonempty_selector": &o.NonemptySelector, "source_identity_selector": &o.SourceSelector, "source_url_selector": &o.SourceURLSelector} {
		*dest, err = inlineSelector(m, key)
		if err != nil {
			return o, err
		}
	}
	if (o.EmptyText == "") != (o.EmptySelector == "") || o.EmptyText == "" && (o.NonemptySelector != "" || o.EmptyRequiresNoJobs) {
		return o, ErrOptions
	}
	for key, dest := range map[string]*string{"source_identity_attribute": &o.SourceAttribute, "source_url_attribute": &o.SourceURLAttribute} {
		*dest, err = inlineOptionalText(m, key, 128)
		if err != nil || *dest != "" && !inlineAttributeName.MatchString(*dest) {
			return o, ErrOptions
		}
	}
	o.SourcePattern, err = inlinePattern(m, "source_identity_regex", "", 1)
	if err != nil {
		return o, err
	}
	if o.SourceSelector == "" && (o.SourceAttribute != "" || o.SourcePattern != nil) || o.SourceSelector != "" && (o.SourceAttribute == "" || o.SourcePattern == nil) || (o.SourceURLSelector == "") != (o.SourceURLAttribute == "") {
		return o, ErrOptions
	}
	if o.SourceSelector != "" && o.StableField != "" || o.SourceURLSelector != "" && (o.SourceSelector != "" || o.StableField != "" || o.Positions != 1) {
		return o, ErrOptions
	}
	// Click expansion requires separate authenticated, out-of-band browser identities.
	// The ordinary document parser cannot manufacture those from provider markup.
	for _, key := range []string{"detail_click_selector", "detail_content_selector", "detail_identity_selector", "detail_identity_attribute", "detail_identity_regex"} {
		if m[key] != nil {
			return o, ErrOptions
		}
	}
	for key, dest := range map[string]*dom.Object{"item_boundary": &o.Item, "section_start": &o.Start, "section_end": &o.End} {
		if m[key] != nil {
			v, ok := m[key].(map[string]any)
			if !ok {
				return o, ErrOptions
			}
			*dest = v
			if ValidateInlineBoundary(*dest) != nil {
				return o, ErrOptions
			}
		}
	}
	if m["item_boundary_tag"] != nil {
		tag, err := inlineOptionalText(m, "item_boundary_tag", 32)
		if err != nil || o.Item != nil {
			return o, ErrOptions
		}
		o.Item = dom.Object{"tag": tag}
		if ValidateInlineBoundary(o.Item) != nil {
			return o, ErrOptions
		}
	}
	if (o.Start == nil) != (o.End == nil) {
		return o, ErrOptions
	}
	if detailTruthy(m["steps"]) {
		a, ok := m["steps"].([]any)
		if !ok || len(a) > 500 {
			return o, ErrOptions
		}
		for _, s := range a {
			v, ok := s.(map[string]any)
			if !ok {
				return o, ErrOptions
			}
			o.Steps = append(o.Steps, v)
		}
	} else if o.EmptyText != "" || o.RequireZeroProof {
		return o, ErrOptions
	}
	if m["valid_through_patterns"] != nil {
		if m["valid_through_regex"] != nil {
			return o, ErrOptions
		}
		a, ok := m["valid_through_patterns"].([]any)
		if !ok || len(a) == 0 || len(a) > 8 {
			return o, ErrOptions
		}
		for _, v := range a {
			item, ok := v.(map[string]any)
			if !ok {
				return o, ErrOptions
			}
			for k := range item {
				if k != "regex" && k != "format" {
					return o, ErrOptions
				}
			}
			re, err := inlinePattern(item, "regex", "(?is)", -1)
			if err != nil || re == nil {
				return o, ErrOptions
			}
			format, err := inlineOptionalText(item, "format", 128)
			if err != nil {
				return o, err
			}
			o.Deadlines = append(o.Deadlines, inlineDeadlinePattern{re, format})
		}
	} else {
		re, err := inlinePattern(m, "valid_through_regex", "(?is)", -1)
		if err != nil {
			return o, err
		}
		if re != nil {
			o.Deadlines = append(o.Deadlines, inlineDeadlinePattern{re, o.DateFormat})
		}
	}
	return o, nil
}

// DecodeInlineMetadata preserves JSON integers for configuration validation.
func DecodeInlineMetadata(raw string) (map[string]any, error) {
	var m map[string]any
	dec := json.NewDecoder(strings.NewReader(raw))
	dec.UseNumber()
	if dec.Decode(&m) != nil || m == nil {
		return nil, ErrOptions
	}
	var extra any
	if dec.Decode(&extra) != io.EOF {
		return nil, ErrOptions
	}
	return m, nil
}
