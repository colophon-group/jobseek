package apisniffer

import (
	stdhtml "html"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf16"
	"unicode/utf8"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"golang.org/x/net/html"
	"golang.org/x/text/cases"
)

type StaticProviderDetailOptions struct{ Provider, Source, Endpoint string }

var linkedInGuestID = regexp.MustCompile(`^/jobs-guest/jobs/api/jobPosting/([\p{Nd}]{1,64})$`)
var jazzDetailPath = regexp.MustCompile(`(?i)^/apply/jobs/details/[A-Za-z0-9_-]+/?$`)
var jazzDetailTenant = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var enterpriseDetailPath = regexp.MustCompile(`^/careersection/[a-z0-9_-]{1,64}/jobdetail\.ftl$`)
var enterpriseJobID = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,127}$`)
var linkedInCriterion = regexp.MustCompile(`[^a-z0-9]+`)
var linkedInDetailCompany = regexp.MustCompile(`^https?://(?:[\p{L}\p{N}_-]+\.)?linkedin\.com/company/([^/?#]+)`)
var enterpriseFillMarker = regexp.MustCompile(`api\.fillList\([\s\p{Z}\x{0085}\x{001c}-\x{001f}]*['"]requisitionDescriptionInterface['"][\s\p{Z}\x{0085}\x{001c}-\x{001f}]*,[\s\p{Z}\x{0085}\x{001c}-\x{001f}]*['"]descRequisition['"][\s\p{Z}\x{0085}\x{001c}-\x{001f}]*,[\s\p{Z}\x{0085}\x{001c}-\x{001f}]*`)
var enterpriseWorkplace = regexp.MustCompile(`#li[-_ ]?(hybrid|remote|on[- ]?site)`)

func StaticProviderDetailOptionsForSource(provider, source string) (StaticProviderDetailOptions, error) {
	o := StaticProviderDetailOptions{Provider: provider, Source: source, Endpoint: source}
	p, ok := ParsePythonURL(source)
	u, err := url.Parse("https://" + p.Host)
	if !ok || len(source) > 8192 || p.Scheme != "https" || err != nil || u.User != nil || u.Port() != "" && u.Port() != "443" {
		return o, ErrOptions
	}
	host := strings.ToLower(u.Hostname())
	switch provider {
	case "linkedin":
		if !linkedInHost(host) || u.Port() != "" {
			return o, ErrOptions
		}
		path := strings.TrimRight(p.Path, "/")
		m := linkedInGuestID.FindStringSubmatch(path)
		if m == nil && strings.Contains(path, "/jobs/view/") {
			m = linkedInPathID.FindStringSubmatch(path)
		}
		if m == nil || !linkedInDigits(m[1]) {
			return o, ErrOptions
		}
		o.Endpoint = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + m[1]
	case "jazzhr":
		tenant := strings.TrimSuffix(host, ".applytojob.com")
		if !strings.HasSuffix(host, ".applytojob.com") || strings.Count(host, ".") != 2 || !jazzDetailTenant.MatchString(tenant) || map[string]bool{"app": true, "developers": true, "login": true, "portal": true, "support": true, "www": true}[tenant] || !jazzDetailPath.MatchString(p.Path) {
			return o, ErrOptions
		}
	case "taleo":
		q, err := url.ParseQuery(p.Query)
		if err != nil || len(q["job"]) == 0 || !enterpriseDetailPath.MatchString(apiIgnoreCase(p.Path)) || !enterpriseJobID.MatchString(apiIgnoreCase(q["job"][0])) || !strings.HasSuffix(host, ".taleo.net") || strings.HasSuffix(host, ".tbe.taleo.net") || p.Fragment != "" {
			return o, ErrOptions
		}
	default:
		return o, ErrOptions
	}
	return o, nil
}
func (o StaticProviderDetailOptions) Request() Request {
	return Request{Method: http.MethodGet, URL: o.Endpoint}
}
func (o StaticProviderDetailOptions) ResourceMatches(resource string) bool {
	return resource == o.Endpoint
}
func emptyStaticDetail() map[string]any {
	return map[string]any{"title": nil, "description": nil, "locations": nil, "employment_type": nil, "job_location_type": nil, "date_posted": nil, "base_salary": nil, "language": nil, "localizations": nil, "extras": nil, "metadata": nil}
}
func staticNullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func ParseLinkedInDetail(source string) (map[string]any, error) {
	if len(source) > 25_000_000 {
		return nil, ErrInventory
	}
	tree, err := html.Parse(strings.NewReader(source))
	if err != nil {
		return nil, err
	}
	out := emptyStaticDetail()
	out["title"] = staticNullable(linkedInNodeText(linkedInSelect(tree, ".top-card-layout__title")))
	if node := linkedInSelect(tree, ".show-more-less-html__markup"); node != nil {
		s, err := dom.InnerHTML(node)
		if err != nil {
			return nil, err
		}
		out["description"] = staticNullable(inlineTrim(s))
	}
	location := linkedInNodeText(linkedInSelect(tree, ".topcard__flavor--bullet"))
	if location != "" {
		out["locations"] = []string{location}
	}
	lower := cases.Fold().String(location)
	if strings.Contains(lower, "hybrid") {
		out["job_location_type"] = "hybrid"
	} else if strings.Contains(lower, "remote") {
		out["job_location_type"] = "remote"
	}
	criteria := map[string]any{}
	for _, item := range inlineSelectedNodes(tree, ".description__job-criteria-item") {
		header := linkedInSelect(item, ".description__job-criteria-subheader")
		value := linkedInSelect(item, ".description__job-criteria-text")
		if header == nil || value == nil {
			continue
		}
		key := strings.Trim(linkedInCriterion.ReplaceAllString(cases.Fold().String(linkedInNodeText(header)), "_"), "_")
		text := linkedInNodeText(value)
		if key != "" && text != "" {
			criteria[key] = text
		}
	}
	if employment, ok := criteria["employment_type"]; ok {
		out["employment_type"] = employment
		delete(criteria, "employment_type")
	}
	if company := linkedInSelect(tree, ".topcard__org-name-link"); company != nil {
		if m := linkedInDetailCompany.FindStringSubmatch(linkedInAttr(company, "href")); m != nil {
			criteria["linkedin_company_slug"] = m[1]
		}
	}
	if len(criteria) > 0 {
		out["metadata"] = criteria
	}
	return out, nil
}

var jazzDetailFallback = dom.Object{"steps": []any{map[string]any{"tag": "h1", "attr": "class=job_title", "field": "title"}, map[string]any{"tag": "h3", "attr": "class=job_meta", "field": "metadata.job_meta", "optional": true}, map[string]any{"field": "description", "html": true, "stop": "Apply Now"}}}

func ParseJazzHRDetail(source string) (map[string]any, error) {
	if len(source) > 25_000_000 {
		return nil, ErrInventory
	}
	content, err := jsonld.Parse("", []byte(source), nil)
	if err != nil {
		return nil, err
	}
	if detailTruthy(content["title"]) && detailTruthy(content["description"]) && detailTruthy(content["locations"]) && detailTruthy(content["employment_type"]) {
		return content, nil
	}
	fallback, err := dom.Parse(source, jazzDetailFallback, nil)
	if err != nil {
		return nil, err
	}
	if !detailTruthy(content["title"]) {
		content["title"] = fallback["title"]
	}
	if !detailTruthy(content["description"]) {
		content["description"] = fallback["description"]
	}
	metadata, _ := fallback["metadata"].(map[string]any)
	raw, _ := metadata["job_meta"].(string)
	parts := []string{}
	for _, s := range strings.Split(raw, " - ") {
		if s = inlineTrim(s); s != "" {
			parts = append(parts, s)
		}
	}
	if len(parts) >= 2 {
		location, employment := parts[len(parts)-2], parts[len(parts)-1]
		if !detailTruthy(content["locations"]) {
			content["locations"] = []string{location}
		}
		if !detailTruthy(content["employment_type"]) {
			content["employment_type"] = employment
		}
		lower := cases.Fold().String(location)
		if !detailTruthy(content["job_location_type"]) && (lower == "hybrid" || lower == "remote") {
			content["job_location_type"] = location
		}
	}
	return content, nil
}

func enterpriseSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func ParseEnterpriseFillList(source string) ([]string, error) {
	if len(source) > 8_000_000 || utf8.RuneCountInString(source) > 2_000_000 {
		return nil, ErrInventory
	}
	marker := enterpriseFillMarker.FindStringIndex(source)
	if marker == nil {
		return nil, nil
	}
	runes := []rune(source)
	index := utf8.RuneCountInString(source[:marker[1]])
	if index >= len(runes) || runes[index] != '[' {
		return nil, nil
	}
	index++
	values := []string{}
	for index < len(runes) {
		for index < len(runes) && enterpriseSpace(runes[index]) {
			index++
		}
		if index < len(runes) && runes[index] == ']' {
			return values, nil
		}
		if len(values) >= 128 || index >= len(runes) || runes[index] != '\'' && runes[index] != '"' {
			return nil, ErrInventory
		}
		quote := runes[index]
		index++
		chars := []rune{}
		units := 0
		closed := false
		for index < len(runes) {
			char := runes[index]
			index++
			if char == quote {
				closed = true
				break
			}
			if char == '\\' {
				if index >= len(runes) {
					return nil, ErrInventory
				}
				char = runes[index]
				index++
				switch char {
				case 'b':
					char = '\b'
				case 'f':
					char = '\f'
				case 'n':
					char = '\n'
				case 'r':
					char = '\r'
				case 't':
					char = '\t'
				case 'v':
					char = '\v'
				case '0':
					char = 0
				case 'x', 'u':
					digits := 2
					if char == 'u' {
						digits = 4
					}
					if index+digits > len(runes) {
						return nil, ErrInventory
					}
					raw := string(runes[index : index+digits])
					n, err := strconv.ParseUint(raw, 16, 16)
					if err != nil || len(raw) != digits {
						return nil, ErrInventory
					}
					char = rune(n)
					index += digits
				case '\n', '\r':
					if char == '\r' && index < len(runes) && runes[index] == '\n' {
						index++
					}
					units++
					if units > 1_000_000 {
						return nil, ErrInventory
					}
					continue
				}
			}
			units++
			if units > 1_000_000 {
				return nil, ErrInventory
			}
			chars = append(chars, char)
		}
		if !closed {
			return nil, ErrInventory
		}
		// JSON transport represents a valid escaped UTF-16 pair as its Unicode
		// scalar. An isolated surrogate cannot become valid canonical text.
		decoded := []rune{}
		for i := 0; i < len(chars); i++ {
			char := chars[i]
			if utf16.IsSurrogate(char) {
				if i+1 >= len(chars) {
					return nil, ErrInventory
				}
				char = utf16.DecodeRune(char, chars[i+1])
				if char == unicode.ReplacementChar {
					return nil, ErrInventory
				}
				i++
			}
			decoded = append(decoded, char)
		}
		values = append(values, string(decoded))
		for index < len(runes) && enterpriseSpace(runes[index]) {
			index++
		}
		if index < len(runes) && runes[index] == ',' {
			index++
			continue
		}
		if index < len(runes) && runes[index] == ']' {
			return values, nil
		}
		return nil, ErrInventory
	}
	return nil, ErrInventory
}

func enterpriseUnquote(s string) string {
	b := strings.Builder{}
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) {
			n, err := strconv.ParseUint(s[i+1:i+3], 16, 8)
			if err == nil {
				b.WriteByte(byte(n))
				i += 2
				continue
			}
		}
		b.WriteByte(s[i])
	}
	// Python urllib.parse.unquote replaces each invalid UTF-8 sequence.
	// strings.ToValidUTF8 groups adjacent invalid bytes into one replacement.
	decoded := b.String()
	b.Reset()
	for len(decoded) > 0 {
		r, size := utf8.DecodeRuneInString(decoded)
		b.WriteRune(r)
		decoded = decoded[size:]
	}
	return b.String()
}
func enterpriseHTML(s string) string {
	return stdhtml.UnescapeString(inlineTrim(enterpriseUnquote(strings.TrimPrefix(s, "!*!"))))
}
func ParseTaleoEnterpriseDetail(source string) (map[string]any, error) {
	values, err := ParseEnterpriseFillList(source)
	if err != nil {
		return nil, err
	}
	out := emptyStaticDetail()
	if len(values) < 29 {
		return out, nil
	}
	out["title"] = staticNullable(inlineTrim(stdhtml.UnescapeString(enterpriseUnquote(values[9]))))
	wipo := -1
	for _, i := range []int{20, 22} {
		if len(values) > i && strings.HasPrefix(values[i], "!*!") {
			wipo = i
			break
		}
	}
	descriptionSlots := []int{11, 13}
	locationSlot, expirySlot := 17, 27
	if wipo >= 0 {
		descriptionSlots = []int{wipo}
		locationSlot, expirySlot = wipo-5, wipo-2
		out["date_posted"] = staticNullable(inlineTrim(stdhtml.UnescapeString(values[wipo-4])))
	} else {
		out["employment_type"] = staticNullable(inlineTrim(stdhtml.UnescapeString(values[23])))
	}
	parts := []string{}
	for _, i := range descriptionSlots {
		if s := enterpriseHTML(values[i]); s != "" {
			parts = append(parts, s)
		}
	}
	description := strings.Join(parts, "\n")
	out["description"] = staticNullable(description)
	if location := inlineTrim(stdhtml.UnescapeString(values[locationSlot])); location != "" {
		out["locations"] = []string{location}
	}
	if m := enterpriseWorkplace.FindStringSubmatch(apiIgnoreCase(description)); m != nil {
		out["job_location_type"] = strings.ReplaceAll(strings.ReplaceAll(m[1], "-", ""), " ", "")
	}
	extras := map[string]any{}
	if wipo < 0 {
		if s := enterpriseHTML(values[13]); s != "" {
			extras["qualifications"] = s
		}
	}
	if expiry := inlineTrim(stdhtml.UnescapeString(values[expirySlot])); expiry != "" {
		extras["valid_through"] = expiry
	}
	if len(extras) > 0 {
		out["extras"] = extras
	}
	metadata := map[string]any{"ats_job_id": values[0], "requisition_number": values[10]}
	put := func(key string, i int) {
		if s := inlineTrim(stdhtml.UnescapeString(values[i])); s != "" {
			metadata[key] = s
		}
	}
	if wipo >= 0 {
		put("organisation", 11)
		if wipo == 22 {
			put("grade", 12)
			put("contract_duration", 14)
		} else {
			put("contract_duration", 12)
		}
	} else {
		put("business_area", 15)
		put("organisation", 21)
	}
	out["metadata"] = metadata
	return out, nil
}
func ParseStaticProviderDetail(provider, source string) (map[string]any, error) {
	switch provider {
	case "linkedin":
		return ParseLinkedInDetail(source)
	case "jazzhr":
		return ParseJazzHRDetail(source)
	case "taleo":
		return ParseTaleoEnterpriseDetail(source)
	}
	return nil, ErrOptions
}
