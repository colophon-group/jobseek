package jsonld

import (
	"bytes"
	"encoding/json"
	"errors"
	stdhtml "html"
	"regexp"
	"strings"
	"unicode/utf8"

	"golang.org/x/net/html"
)

type Request struct {
	URL    string         `json:"url"`
	HTML   string         `json:"html"`
	Config map[string]any `json:"config"`
}

var tagsRE = regexp.MustCompile(`<[^>]+>`)
var commaRE = regexp.MustCompile(`(?s)("datePosted"\s*:\s*"(?:\\.|[^"\\])*")(\s*)("hiringOrganization"\s*:)`)
var descriptionEntitiesRE = regexp.MustCompile(`(?i)&amp;#(?:(?:x0*(?:9|a|d))|(?:0*(?:9|10|13)));`)

func clean(v any) any {
	s, ok := v.(string)
	if !ok {
		return nil
	}
	for range 2 {
		next := stdhtml.UnescapeString(s)
		if next == s {
			break
		}
		s = next
	}
	s = words(s)
	if s == "" {
		return nil
	}
	return s
}
func strip(s string) string { return trim(tagsRE.ReplaceAllString(s, "")) }
func cdata(raw string) string {
	raw = trim(raw)
	for _, pair := range [][2]string{{"//<![CDATA[", "//]]>"}, {"/*<![CDATA[*/", "/*]]>*/"}, {"<![CDATA[", "]]>"}} {
		if strings.HasPrefix(raw, pair[0]) && strings.HasSuffix(raw, pair[1]) {
			return trim(raw[len(pair[0]) : len(raw)-len(pair[1])])
		}
	}
	return raw
}
func repairDollar(s string) string {
	var b strings.Builder
	inString := false
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c == '"' {
			inString = !inString
		}
		if inString && c == '\\' && i+1 < len(s) {
			i++
			if s[i] != '$' {
				b.WriteByte('\\')
			}
			b.WriteByte(s[i])
			continue
		}
		b.WriteByte(c)
	}
	return b.String()
}
func repairControls(s string) string {
	var b strings.Builder
	inside, escape := false, false
	for _, c := range s {
		if escape {
			b.WriteRune(c)
			escape = false
			continue
		}
		if c == '\\' {
			b.WriteRune(c)
			escape = true
			continue
		}
		if c == '"' {
			inside = !inside
		}
		if inside && c < 32 {
			switch c {
			case '\n':
				b.WriteString(`\n`)
			case '\r':
				b.WriteString(`\r`)
			case '\t':
				b.WriteString(`\t`)
			}
			continue
		}
		b.WriteRune(c)
	}
	return b.String()
}
func parseBlock(s string) any {
	s = cdata(s)
	if s == "" {
		return nil
	}
	for _, candidate := range []string{s, stdhtml.UnescapeString(s)} {
		repaired := commaRE.ReplaceAllString(candidate, "${1},${2}${3}")
		for _, v := range []string{candidate, repaired, repairDollar(candidate), repairDollar(repaired)} {
			value, err := decodeJSON([]byte(v))
			if err == nil && value != nil {
				return value
			}
			value, err = decodeJSON([]byte(repairControls(v)))
			if err == nil && value != nil {
				return value
			}
		}
	}
	return nil
}

func normalize(v any) any {
	switch x := v.(type) {
	case orderedObject:
		out := orderedObject{values: map[string]any{}}
		for _, k := range x.keys {
			nk := k
			if !strings.HasPrefix(k, "@") && k != "" {
				r, n := utf8.DecodeRuneInString(k)
				nk = strings.ToLower(string(r)) + k[n:]
			}
			if _, exists := out.values[nk]; !exists {
				out.keys = append(out.keys, nk)
			}
			out.values[nk] = normalize(x.values[k])
		}
		return out
	case []any:
		out := make([]any, len(x))
		for i, v := range x {
			out[i] = normalize(v)
		}
		return out
	}
	return v
}
func posting(v any) any {
	if list, ok := v.([]any); ok {
		for _, node := range list {
			if p := posting(node); p != nil {
				return p
			}
		}
		return nil
	}
	o, ok := v.(orderedObject)
	if !ok {
		return nil
	}
	typ := o.values["@type"]
	found := strings.Contains(text(typ), "JobPosting")
	if list, ok := typ.([]any); ok {
		for _, node := range list {
			found = found || strings.Contains(text(node), "JobPosting")
		}
	}
	if found {
		return normalize(o)
	}
	if graph, ok := o.values["@graph"].([]any); ok {
		return posting(graph)
	}
	return nil
}

func placeholders(s string) bool {
	switch strings.ToLower(s) {
	case "unavailable", "not available", "n/a", "none", "null", "-":
		return true
	}
	return false
}
func locations(p any, ignoreRegion bool) any {
	raw := get(p, "jobLocation")
	if raw == nil {
		return nil
	}
	list, ok := raw.([]any)
	if !ok {
		list = []any{raw}
	}
	org := text(clean(get(get(p, "hiringOrganization"), "name")))
	seen := map[string]bool{}
	out := []string{}
	for _, v := range list {
		if _, ok := v.(orderedObject); !ok {
			continue
		}
		name := text(clean(get(v, "name")))
		if placeholders(name) {
			name = ""
		}
		address := ""
		switch a := get(v, "address").(type) {
		case string:
			address = text(clean(a))
		case orderedObject:
			fields := []string{"addressLocality"}
			if !ignoreRegion {
				fields = append(fields, "addressRegion")
			}
			fields = append(fields, "addressCountry")
			parts := []string{}
			for _, k := range fields {
				value := a.values[k]
				if !pyTruthy(value) {
					continue
				}
				if _, ok := value.(orderedObject); ok {
					value = get(value, "name")
				}
				s := text(clean(value))
				if s != "" && !placeholders(s) {
					parts = append(parts, s)
				}
			}
			address = strings.Join(parts, ", ")
		}
		value := name
		if address != "" && (value == "" || (org != "" && strings.EqualFold(name, org))) {
			value = address
		}
		if value != "" && !seen[value] {
			out = append(out, value)
			seen[value] = true
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

var commasRE = regexp.MustCompile(`\s*,\s*`)

func metaLocations(raw string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, chunk := range strings.FieldsFunc(raw, func(r rune) bool { return r == ';' || r == '|' }) {
		parts := []string{}
		for _, v := range strings.Split(chunk, "~") {
			if trim(v) != "" {
				parts = append(parts, trim(v))
			}
		}
		s := trim(chunk)
		if len(parts) > 0 {
			s = strings.Join(parts, ", ")
		}
		s = words(s)
		s = strings.Trim(commasRE.ReplaceAllString(s, ", "), " ,")
		if s != "" && !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	return out
}
func fallbackLocations(meta map[string]string) any {
	for _, k := range []string{"gtm_tbcn_location", "dimension7"} {
		v := metaLocations(meta[k])
		if len(v) > 0 {
			return v
		}
	}
	primary := []string{}
	for _, k := range []string{"job-city", "job-region", "job-country"} {
		s := text(clean(meta[k]))
		if s != "" {
			primary = append(primary, s)
		}
	}
	list := []string{}
	if len(primary) > 0 {
		list = append(list, strings.Join(primary, ", "))
	}
	list = append(list, metaLocations(meta["job-secondarylocations"])...)
	out := []string{}
	seen := map[string]bool{}
	for _, s := range list {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
func salaryUnit(v any) any {
	if v == nil {
		return nil
	}
	s, ok := v.(string)
	if !ok {
		return nil
	}
	s = strings.ToLower(trim(s))
	if s == "" {
		return nil
	}
	direct := map[string]string{"yr": "year", "mo": "month", "hr": "hour"}
	if v, ok := direct[s]; ok {
		return v
	}
	for _, pair := range [][2]string{{"two_weeks", "week"}, {"biweekly", "week"}, {"hour", "hour"}, {"month", "month"}, {"week", "week"}, {"year", "year"}, {"annual", "year"}, {"daily", "day"}, {"day", "day"}} {
		if strings.Contains(s, pair[0]) {
			return pair[1]
		}
	}
	return nil
}
func salary(p any) any {
	b, ok := get(p, "baseSalary").(orderedObject)
	if !ok {
		return nil
	}
	outer := salaryUnit(b.values["unitText"])
	value := b.values["value"]
	switch v := value.(type) {
	case orderedObject:
		unit := salaryUnit(v.values["unitText"])
		if unit == nil {
			unit = outer
		}
		return map[string]any{"currency": b.values["currency"], "min": v.values["minValue"], "max": v.values["maxValue"], "unit": unit}
	case json.Number, bool:
		return map[string]any{"currency": b.values["currency"], "min": v, "max": v, "unit": outer}
	}
	return nil
}
func textList(v any) any {
	if s, ok := v.(string); ok {
		if trim(s) == "" {
			return nil
		}
		return []string{s}
	}
	if list, ok := v.([]any); ok {
		out := []string{}
		for _, item := range list {
			if !pyTruthy(item) {
				continue
			}
			s, ok := item.(string)
			if !ok {
				s = pythonRepr(item)
			}
			out = append(out, trim(s))
		}
		if len(out) > 0 {
			return out
		}
	}
	return nil
}
func employment(v any) any {
	if s, ok := v.(string); ok {
		s = trim(s)
		if s != "" {
			return s
		}
		return nil
	}
	list, ok := v.([]any)
	if !ok {
		return nil
	}
	values := []string{}
	tokens := map[string]string{}
	for _, x := range list {
		s, ok := x.(string)
		if !ok {
			continue
		}
		s = trim(s)
		if s == "" {
			continue
		}
		values = append(values, s)
		token := strings.ToUpper(strings.Join(strings.FieldsFunc(s, func(r rune) bool { return pySpace(r) || r == '-' }), "_"))
		tokens[token] = s
	}
	if len(values) == 0 {
		return nil
	}
	for _, k := range []string{"INTERN", "TEMPORARY", "CONTRACTOR", "VOLUNTEER", "PER_DIEM"} {
		if s, ok := tokens[k]; ok {
			return s
		}
	}
	if tokens["FULL_TIME"] != "" && tokens["PART_TIME"] != "" {
		return "FULL_TIME, PART_TIME"
	}
	for _, k := range []string{"PART_TIME", "FULL_TIME", "OTHER"} {
		if s, ok := tokens[k]; ok {
			return s
		}
	}
	return values[0]
}
func fromPosting(p any, ignoreRegion bool) map[string]any {
	result := emptyContent()
	name := get(p, "title")
	if !pyTruthy(name) {
		name = get(p, "name")
	}
	result["title"] = clean(name)
	if s, ok := get(p, "description").(string); ok {
		result["description"] = descriptionEntitiesRE.ReplaceAllStringFunc(s, func(s string) string { return stdhtml.UnescapeString(stdhtml.UnescapeString(s)) })
	}
	result["locations"] = locations(p, ignoreRegion)
	result["employment_type"] = employment(get(p, "employmentType"))
	result["job_location_type"] = get(p, "jobLocationType")
	result["date_posted"] = get(p, "datePosted")
	result["base_salary"] = salary(p)
	extras := map[string]any{}
	for _, pair := range [][2]string{{"skills", "skills"}, {"responsibilities", "responsibilities"}, {"qualifications", "qualifications"}} {
		v := get(p, pair[1])
		if pair[0] == "qualifications" && !pyTruthy(v) {
			v = get(p, "educationRequirements")
		}
		if val := textList(v); val != nil {
			extras[pair[0]] = val
		}
	}
	if v := get(p, "validThrough"); pyTruthy(v) {
		extras["valid_through"] = v
	}
	if len(extras) > 0 {
		result["extras"] = extras
	}
	return result
}

func documentBlocks(body []byte) ([]any, map[string]string, string) {
	z := html.NewTokenizer(bytes.NewReader(body))
	meta := map[string]string{}
	blocks := []any{}
	inScript, inTitle := false, false
	script, pageTitle := "", ""
	for {
		typ := z.Next()
		if typ == html.ErrorToken {
			break
		}
		raw := append([]byte(nil), z.Raw()...)
		switch typ {
		case html.StartTagToken, html.SelfClosingTagToken:
			tag := z.Token().Data
			attrs := pythonAttributes(raw)
			if tag == "meta" {
				key := attrs["name"]
				if key == "" {
					key = attrs["property"]
				}
				if key != "" && attrs["content"] != "" {
					meta[strings.ToLower(key)] = attrs["content"]
				}
			}
			if tag == "script" && attrs["type"] == "application/ld+json" {
				inScript = true
				script = ""
			} else if tag == "title" {
				inTitle = true
				pageTitle = ""
			}
			if typ == html.SelfClosingTagToken {
				if tag == "script" {
					inScript = false
				}
				if tag == "title" {
					inTitle = false
				}
			}
		case html.TextToken:
			if inScript {
				script += string(raw)
			} else if inTitle {
				pageTitle += string(z.Text())
			}
		case html.EndTagToken:
			tag := z.Token().Data
			if tag == "title" {
				inTitle = false
			}
			if tag == "script" && inScript {
				inScript = false
				if v := parseBlock(script); v != nil {
					blocks = append(blocks, v)
				}
			}
		}
	}
	return blocks, meta, pageTitle
}

func Parse(rawURL string, body []byte, config map[string]any) (map[string]any, error) {
	blocks, meta, pageTitle := documentBlocks(body)
	result := emptyContent()
	ignoreLoc := isTrue(config["ignore_locations"])
	for _, block := range blocks {
		p := posting(block)
		if p == nil {
			continue
		}
		result = fromPosting(p, isTrue(config["ignore_address_region"]))
		if isTrue(config["ignore_date_posted"]) {
			result["date_posted"] = nil
		}
		if isTrue(config["ignore_valid_through"]) {
			if extras, ok := result["extras"].(map[string]any); ok {
				delete(extras, "valid_through")
				if len(extras) == 0 {
					result["extras"] = nil
				}
			}
		}
		if ignoreLoc {
			result["locations"] = nil
		}
		if !pyTruthy(result["title"]) {
			title := text(clean(pageTitle))
			org := text(clean(get(get(p, "hiringOrganization"), "name")))
			if title != "" && org != "" {
				for _, sep := range []string{" - ", " | ", " – "} {
					suffix := sep + org
					if len(title) >= len(suffix) && strings.EqualFold(title[len(title)-len(suffix):], suffix) {
						result["title"] = clean(title[:len(title)-len(suffix)])
						break
					}
				}
			}
		}
		if !pyTruthy(result["locations"]) && !ignoreLoc {
			result["locations"] = fallbackLocations(meta)
		}
		return defaultsByURL(result, config, rawURL)
	}
	if title, desc := clean(meta["job-title"]), meta["job-description"]; title != nil && desc != "" && strip(desc) != "" {
		result["title"] = title
		result["description"] = desc
		if !ignoreLoc {
			result["locations"] = fallbackLocations(meta)
		}
		result["job_location_type"] = clean(meta["job-workingmode"])
		result["date_posted"] = clean(meta["job-posteddate"])
		metadata := map[string]any{}
		for _, pair := range [][2]string{{"requisition_id", "job-id"}, {"job_function", "job-function"}, {"experience_level", "job-experiencelevel"}} {
			if v := clean(meta[pair[1]]); v != nil {
				metadata[pair[0]] = v
			}
		}
		if len(metadata) > 0 {
			result["metadata"] = metadata
		}
	}
	return defaultsByURL(result, config, rawURL)
}
func defaultsByURL(content map[string]any, config map[string]any, rawURL string) (map[string]any, error) {
	raw := config["defaults_by_url"]
	if raw == nil {
		return content, nil
	}
	defaults, ok := raw.(map[string]any)
	if !ok {
		return nil, errors.New("JSON-LD defaults_by_url must be an object")
	}
	for _, v := range defaults {
		if _, ok := v.(map[string]any); !ok {
			return nil, errors.New("JSON-LD defaults_by_url must map URLs to objects")
		}
	}
	if values, ok := defaults[rawURL].(map[string]any); ok {
		for k, v := range values {
			current, exists := content[k]
			if !exists {
				return nil, errors.New("unknown JSON-LD default field")
			}
			if missing(current) {
				content[k] = v
			}
		}
	}
	return content, nil
}

func isTrue(v any) bool { b, ok := v.(bool); return ok && b }
func missing(v any) bool {
	switch x := v.(type) {
	case nil:
		return true
	case string:
		return x == ""
	case []any:
		return len(x) == 0
	case []string:
		return len(x) == 0
	}
	return false
}

// ContainsJobPosting requires a structured JobPosting and, when configured,
// its normalized hiringOrganization.name. Meta tags and arbitrary text do not
// establish the tenant witness. Each block preserves the parser's first-posting
// selection and existing narrow malformed-JSON repairs.
func ContainsJobPosting(body []byte, expectedOrganization string) bool {
	blocks, _, _ := documentBlocks(body)
	for _, block := range blocks {
		p := posting(block)
		if p == nil {
			continue
		}
		if expectedOrganization == "" {
			return true
		}
		if text(clean(get(get(p, "hiringOrganization"), "name"))) == expectedOrganization {
			return true
		}
	}
	return false
}
