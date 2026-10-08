package apisniffer

import (
	"encoding/json"
	"math"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"github.com/dlclark/regexp2/v2"
)

type PDFOptions struct {
	TitleSource                                                      string
	TitlePattern, LocationPattern, LocationURLPattern, FieldsPattern *regexp2.Regexp
	RequireTitlePattern, RepairSplitInitial, OCR                     bool
	OCRLanguages                                                     string
	OCRScale                                                         int
	Defaults                                                         map[string]any
	Headers                                                          map[string]string
}

func PDFOptionsFromConfig(m map[string]any) (PDFOptions, error) {
	o := PDFOptions{TitleSource: "url", OCRLanguages: "eng", OCRScale: 2, Defaults: map[string]any{}, Headers: map[string]string{}}
	allowed := map[string]bool{}
	for _, k := range []string{"title_source", "title_pattern", "location_pattern", "location_url_pattern", "fields_pattern", "require_title_pattern", "repair_split_initial", "ocr", "ocr_languages", "ocr_scale", "defaults", "request_headers"} {
		allowed[k] = true
	}
	for k := range m {
		if !allowed[k] {
			return o, ErrOptions
		}
	}
	if value, ok := m["title_source"]; ok {
		s, ok := value.(string)
		if !ok || s != "url" && s != "text" {
			return o, ErrOptions
		}
		o.TitleSource = s
	}
	var err error
	for k, dest := range map[string]*bool{"require_title_pattern": &o.RequireTitlePattern, "repair_split_initial": &o.RepairSplitInitial, "ocr": &o.OCR} {
		*dest, err = inlineBool(m, k)
		if err != nil {
			return o, err
		}
	}
	for k, dest := range map[string]**regexp2.Regexp{"title_pattern": &o.TitlePattern, "location_pattern": &o.LocationPattern, "location_url_pattern": &o.LocationURLPattern, "fields_pattern": &o.FieldsPattern} {
		*dest, err = inlinePattern(m, k, "", 0)
		if err != nil {
			return o, err
		}
	}
	if o.RequireTitlePattern && (o.TitleSource != "text" || o.TitlePattern == nil) {
		return o, ErrOptions
	}
	if value, ok := m["ocr_languages"]; ok {
		s, ok := value.(string)
		if !ok {
			return o, ErrOptions
		}
		o.OCRLanguages = strings.TrimSpace(s)
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_+-]{1,64}$`).MatchString(o.OCRLanguages) {
		return o, ErrOptions
	}
	if value, ok := m["ocr_scale"]; ok {
		number, ok := pdfNumber(value)
		n := int(number)
		if !ok || number != float64(n) || n < 1 || n > 4 {
			return o, ErrOptions
		}
		o.OCRScale = n
	}
	if value := m["defaults"]; value != nil {
		defaults, ok := value.(map[string]any)
		if !ok || len(defaults) > 16 {
			return o, ErrOptions
		}
		for k, v := range defaults {
			if !pdfValidDefault(k, v) {
				return o, ErrOptions
			}
			o.Defaults[k] = v
		}
	}
	if value := m["request_headers"]; value != nil {
		headers, ok := value.(map[string]any)
		if !ok {
			return o, ErrOptions
		}
		for k, v := range headers {
			s, ok := v.(string)
			if !ok {
				return o, ErrOptions
			}
			o.Headers[k] = s
		}
		if jsonld.ValidateDocumentOptions(jsonld.DocumentOptions{Headers: o.Headers, PublicHeaders: true}) != nil {
			return o, ErrOptions
		}
		clean := map[string]string{}
		for k, v := range o.Headers {
			clean[strings.TrimSpace(k)] = strings.TrimSpace(v)
		}
		o.Headers = clean
	}
	return o, nil
}

func pdfValidDefault(k string, v any) bool {
	switch k {
	case "title", "description", "date_posted", "language":
		s, ok := v.(string)
		if !ok || strings.TrimSpace(s) == "" {
			return false
		}
		if k == "language" {
			return regexp.MustCompile(`^[a-z]{2}$`).MatchString(s)
		}
		if k == "date_posted" {
			_, err := time.Parse("2006-01-02", s)
			return err == nil
		}
		return true
	case "locations":
		items, ok := v.([]any)
		if !ok || len(items) == 0 {
			return false
		}
		for _, item := range items {
			s, ok := item.(string)
			if !ok || strings.TrimSpace(s) == "" {
				return false
			}
		}
		return true
	case "employment_type":
		return v == "full_time" || v == "part_time" || v == "contract" || v == "temporary" || v == "volunteer" || v == "full_or_part"
	case "job_location_type":
		return v == "onsite" || v == "remote" || v == "hybrid"
	case "extras", "metadata", "localizations":
		_, ok := v.(map[string]any)
		return ok
	case "base_salary":
		m, ok := v.(map[string]any)
		if !ok || len(m) == 0 {
			return false
		}
		for key := range m {
			if key != "currency" && key != "min" && key != "max" && key != "unit" {
				return false
			}
		}
		if value := m["currency"]; value != nil {
			s, ok := value.(string)
			if !ok || !regexp.MustCompile(`^[A-Z]{3}$`).MatchString(s) {
				return false
			}
		}
		if value := m["unit"]; value != nil && value != "year" && value != "month" && value != "week" && value != "day" && value != "hour" {
			return false
		}
		amounts := map[string]float64{}
		for _, key := range []string{"min", "max"} {
			if v := m[key]; v != nil {
				n, ok := pdfNumber(v)
				if !ok || n < 0 || math.IsInf(n, 0) || math.IsNaN(n) {
					return false
				}
				amounts[key] = n
			}
		}
		if len(amounts) == 0 {
			return false
		}
		low, a := amounts["min"]
		high, b := amounts["max"]
		return !a || !b || low <= high
	}
	return false
}

func pdfNumber(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		return n, true
	case json.Number:
		f, e := n.Float64()
		return f, e == nil
	case int:
		return float64(n), true
	}
	return 0, false
}

func pdfCapture(text string, pattern *regexp2.Regexp, group string, repair bool) (string, error) {
	if pattern == nil {
		return "", nil
	}
	match, err := pattern.FindStringMatch(text)
	if err != nil {
		return "", err
	}
	if match == nil {
		return "", nil
	}
	var captured *regexp2.Group
	if group == "" {
		captured = match.GroupByNumber(1)
	} else {
		captured = match.GroupByName(group)
	}
	if captured == nil || len(captured.Captures) == 0 {
		return "", nil
	}
	return pdfNormalizeScalar(captured.String(), repair), nil
}

func pdfWord(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' }
func pdfNormalizeScalar(s string, repair bool) string {
	runes := []rune(s)
	out := []rune{}
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		out = append(out, r)
		if r == '-' && i > 0 && pdfWord(runes[i-1]) {
			j := i + 1
			for j < len(runes) && (runes[j] == ' ' || runes[j] == '\t') {
				j++
			}
			if j < len(runes) && runes[j] == '\r' {
				j++
			}
			if j < len(runes) && runes[j] == '\n' {
				j++
				for j < len(runes) && (runes[j] == ' ' || runes[j] == '\t') {
					j++
				}
				if j < len(runes) && pdfWord(runes[j]) {
					i = j - 1
				}
			}
		} else if repair && r >= 'A' && r <= 'Z' && (i == 0 || !pdfWord(runes[i-1])) {
			j := i + 1
			for j < len(runes) && (runes[j] == ' ' || runes[j] == '\t' || runes[j] == '\r' || runes[j] == '\n') {
				j++
			}
			if j > i+1 && j < len(runes) && unicode.IsLetter(runes[j]) {
				i = j - 1
			}
		}
	}
	return inlineNormalized(string(out))
}

func pdfTitleURL(source string, pattern *regexp2.Regexp) (string, error) {
	decoded, err := url.PathUnescape(source)
	if err != nil {
		return "", ErrOptions
	}
	name := decoded[strings.LastIndex(decoded, "/")+1:]
	if strings.HasSuffix(strings.ToLower(name), ".pdf") {
		name = name[:len(name)-4]
	}
	name = regexp.MustCompile(`^[a-f0-9]{20,}_`).ReplaceAllString(name, "")
	if name == "" {
		return "", nil
	}
	captured, err := pdfCapture(name, pattern, "", false)
	if err != nil {
		return "", err
	}
	if captured != "" {
		name = captured
	}
	return inlineNormalized(strings.NewReplacer("_", " ", "-", " ").Replace(name)), nil
}

func pdfHeading(text string) string {
	checked := 0
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		n := utf8.RuneCountInString(line)
		if n < 3 {
			continue
		}
		checked++
		if checked > 5 || n > 120 {
			break
		}
		r, _ := utf8.DecodeRuneInString(line)
		if strings.ContainsRune("•·‣▪▸–*►", r) || unicode.IsLower(r) {
			continue
		}
		return line
	}
	return ""
}

func pdfTextHTML(text string) string {
	paragraphs := []string{}
	current := []string{}
	flush := func() {
		if len(current) > 0 {
			paragraphs = append(paragraphs, "<p>"+strings.Join(current, " ")+"</p>")
			current = nil
		}
	}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			flush()
		} else {
			current = append(current, line)
		}
	}
	flush()
	return strings.Join(paragraphs, "\n")
}

// PDFTextHasConfiguredTitle distinguishes an explicit publisher title capture
// from the filename/heading fallback when comparing extraction orders.
func PDFTextHasConfiguredTitle(text string, o PDFOptions) (bool, error) {
	if len(text) > 16<<20 || !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
		return false, ErrInventory
	}
	text = strings.TrimSpace(text)
	if o.FieldsPattern != nil {
		title, err := pdfCapture(text, o.FieldsPattern, "title", o.RepairSplitInitial)
		if err != nil || title != "" {
			return title != "", err
		}
	}
	if o.TitleSource == "text" && o.TitlePattern != nil {
		title, err := pdfCapture(text, o.TitlePattern, "", o.RepairSplitInitial)
		return title != "", err
	}
	return o.FieldsPattern == nil, nil
}

// ParsePDFText projects the legacy PDF field contract independently of the
// binary extraction engine. Matching is bounded by the shared regex timeout.
func ParsePDFText(text, source string, o PDFOptions) (map[string]any, error) {
	if len(text) > 16<<20 || !utf8.ValidString(text) || strings.ContainsRune(text, 0) {
		return nil, ErrInventory
	}
	text = strings.TrimSpace(text)
	out := map[string]any{}
	if text == "" && o.RequireTitlePattern {
		return nil, ErrInventory
	}
	title, err := pdfCapture(text, o.FieldsPattern, "title", o.RepairSplitInitial)
	if err != nil {
		return nil, err
	}
	location, err := pdfCapture(text, o.FieldsPattern, "location", o.RepairSplitInitial)
	if err != nil {
		return nil, err
	}
	if o.RequireTitlePattern {
		required, err := pdfCapture(text, o.TitlePattern, "", o.RepairSplitInitial)
		if err != nil || required == "" {
			return nil, ErrInventory
		}
		if title == "" {
			title = required
		}
	}
	if title == "" && o.TitleSource == "text" && text != "" {
		title, err = pdfCapture(text, o.TitlePattern, "", o.RepairSplitInitial)
		if err != nil {
			return nil, err
		}
		if title == "" {
			title = pdfHeading(text)
		}
	}
	if title == "" {
		title, err = pdfTitleURL(source, o.TitlePattern)
		if err != nil {
			return nil, err
		}
	}
	if location == "" && text != "" {
		location, err = pdfCapture(text, o.LocationPattern, "", false)
		if err != nil {
			return nil, err
		}
	}
	if location == "" {
		u, err := url.Parse(source)
		if err != nil {
			return nil, ErrOptions
		}
		name := u.Path[strings.LastIndex(u.Path, "/")+1:]
		location, err = pdfCapture(name, o.LocationURLPattern, "", false)
		if err != nil {
			return nil, err
		}
	}
	if title != "" {
		out["title"] = title
	}
	if location != "" {
		out["locations"] = []any{location}
	}
	if text != "" {
		out["description"] = pdfTextHTML(text)
	}
	for k, v := range o.Defaults {
		if old := out[k]; old == nil || old == "" {
			out[k] = v
		}
	}
	return out, nil
}
