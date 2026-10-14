package apisniffer

import (
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/net/html"
)

var lastPositionID = regexp.MustCompile(`(?i)^[a-f0-9]{24}$`)
var lastTupuPath = regexp.MustCompile(`(?i)^/[a-z0-9_-]+/position/detail$`)
var lastPhuketPath = regexp.MustCompile(`(?i)^/(?:en/)?jobs/([0-9]{6})-([0-9]+)-phuket/[^/?#]+[.]html$`)
var lastThaiDate = regexp.MustCompile(`^([\p{Nd}]{1,2})\s+(\S+)\s+([\p{Nd}]{2,4})$`)
var lastThaiMonths = map[string]int{"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6, "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}

func lastDetailAttribute(n *html.Node, key string) (string, bool) {
	if n == nil {
		return "", false
	}
	return inlineAttribute(n, key)
}

func lastLocation(text string) string {
	r, err := dom.CompileURLPattern(`(?i)(?:Standort|Location)\s*:\s*(.+?)(?=\s*(?:Zeitpunkt|Start(?:\s+date)?|$))`)
	if err != nil {
		return ""
	}
	m, err := r.FindStringMatch(text)
	if err != nil || m == nil {
		return ""
	}
	return strings.Trim(inlineNormalized(m.GroupByNumber(1).String()), " -|,")
}

func ParseOnlyfyDetail(source string, config map[string]any) (map[string]any, error) {
	tree, err := finalDetailTree(source)
	if err != nil {
		return nil, err
	}
	out := emptyStaticDetail()
	title := linkedInSelect(tree, "title")
	if title == nil {
		title = linkedInSelect(tree, "h1.text-element-header_text")
	}
	out["title"] = staticNullable(finalNodeText(title))
	fragments, seen := []string{}, map[string]bool{}
	for _, n := range inlineSelectedNodes(tree, ".text-element-body_text") {
		text := finalSeparatedText(n)
		location := lastLocation(inlineNodeText(n))
		if out["locations"] == nil && location != "" {
			out["locations"] = []string{location}
		}
		// The original excludes location-label fragments even when their
		// extracted value trims to empty.
		pattern, _ := dom.CompileURLPattern(`(?i)(?:Standort|Location)\s*:\s*(.+?)(?=\s*(?:Zeitpunkt|Start(?:\s+date)?|$))`)
		match, _ := pattern.FindStringMatch(text)
		if text == "" || match != nil || seen[text] {
			continue
		}
		seen[text] = true
		inner, e := dom.InnerHTML(n)
		if e != nil {
			return nil, e
		}
		if inner = inlineTrim(inner); inner != "" {
			tag := n.Data
			if !strings.Contains("|p|li|ul|ol|h1|h2|h3|h4|", "|"+tag+"|") {
				tag = "p"
			}
			fragments = append(fragments, "<"+tag+">"+inner+"</"+tag+">")
		}
	}
	meta := linkedInSelect(tree, `meta[name="description"]`)
	if out["locations"] == nil {
		if v, _ := lastDetailAttribute(meta, "content"); lastLocation(v) != "" {
			out["locations"] = []string{lastLocation(v)}
		}
	}
	out["description"] = staticNullable(strings.Join(fragments, "\n"))
	if out["description"] == nil {
		for _, selector := range []string{`meta[name="description"]`, `meta[property="og:description"]`} {
			value, _ := lastDetailAttribute(linkedInSelect(tree, selector), "content")
			value = inlineNormalized(value)
			if utf8.RuneCountInString(value) >= 200 {
				out["description"] = value
				break
			}
		}
	}
	if value, present := config["language"]; present {
		out["language"] = value
	}
	return out, nil
}

// Onlyfy candidate and listing requests retain the original public URL's
// lexical handle and locale; this function supplies no ownership authority.
func OnlyfyDetailURLs(source, language string) (candidate, listing, handle string, err error) {
	u, ok := ParsePythonURL(source)
	if !ok {
		return "", "", "", ErrOptions
	}
	parts := []string{}
	for _, p := range strings.Split(u.Path+func() string {
		if u.Params != "" {
			return ";" + u.Params
		}
		return ""
	}(), "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	index := -1
	for i, p := range parts {
		if p == "job" {
			index = i
			break
		}
	}
	if index < 0 || index+1 == len(parts) || parts[index+1] == "show" || parts[index+1] == "print" {
		return "", "", "", ErrOptions
	}
	handle = parts[index+1]
	if language == "" && index > 0 && regexp.MustCompile(`^[a-zA-Z]{2}$`).MatchString(parts[index-1]) {
		language = strings.ToLower(parts[index-1])
	}
	if language == "" {
		language = "en"
	}
	u.Path = "/job/show/" + strings.ReplaceAll(url.QueryEscape(handle), "+", "%20") + "/full"
	u.Params = ""
	u.Query = "lang=" + strings.ReplaceAll(url.QueryEscape(language), "+", "%20") + "&mode=candidate"
	u.Fragment = ""
	candidate = u.String()
	u.Path = "/" + strings.ReplaceAll(url.QueryEscape(language), "+", "%20")
	u.Query = ""
	return candidate, u.String(), handle, nil
}

func OnlyfyListingLocation(source, handle string) []string {
	d, err := ParseEmbeddedMonitorDocument(source, "rsc")
	if err != nil {
		return nil
	}
	v, err := Search(d.Value, "jobsData.data")
	jobs, ok := v.([]any)
	if err != nil || !ok {
		return nil
	}
	for _, item := range jobs {
		job, ok := item.(map[string]any)
		if !ok {
			continue
		}
		endpoint, _ := job["jobAdUrl"].(string)
		parts := strings.Split(strings.TrimRight(endpoint, "/"), "/")
		if parts[len(parts)-1] == handle {
			if !detailTruthy(job["cityName"]) {
				return nil
			}
			location := inlineTrim(lastPythonScalar(d, job["cityName"]))
			if location != "" {
				return []string{location}
			}
			return nil
		}
	}
	return nil
}

func TupuDetailIdentity(source string) string {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || !strings.HasSuffix(strings.ToLower(u.Hostname()), ".tupu360.com") || u.User != nil || u.Port() != "" || !lastTupuPath.MatchString(u.EscapedPath()) {
		return ""
	}
	values, _ := url.ParseQuery(u.RawQuery)
	ids := []string{}
	for _, value := range values["positionId"] {
		if value != "" {
			ids = append(ids, value)
		}
	}
	if len(ids) != 1 || !lastPositionID.MatchString(ids[0]) {
		return ""
	}
	return strings.ToLower(ids[0])
}

func ParseTupuDetail(source string) (map[string]any, error) {
	tree, err := finalDetailTree(source)
	if err != nil {
		return nil, err
	}
	out := emptyStaticDetail()
	title, _ := lastDetailAttribute(linkedInSelect(tree, "#positionName"), "value")
	title = inlineNormalized(title)
	if title == "" {
		title = inlineNormalized(finalNodeText(linkedInSelect(tree, "#positionTitleNode h5 .txt")))
	}
	out["title"] = staticNullable(title)
	out["description"], err = finalDetailInner(linkedInSelect(tree, ".position-description"))
	if err != nil {
		return nil, err
	}
	for _, n := range inlineSelectedNodes(tree, "dl.position-extend") {
		key, value := linkedInSelect(n, "dt"), linkedInSelect(n, "dd")
		if key == nil || value == nil {
			continue
		}
		s := finalSeparatedText(value)
		switch strings.TrimRight(finalSeparatedText(key), ":：") {
		case "工作地点":
			if out["locations"] == nil && s != "" {
				out["locations"] = []string{s}
			}
		case "发布时间":
			if out["date_posted"] == nil && s != "" {
				out["date_posted"] = s
			}
		}
	}
	metadata := map[string]any{}
	id, _ := lastDetailAttribute(linkedInSelect(tree, "#positionInfoInp"), "data-id")
	if id == "" {
		id, _ = lastDetailAttribute(linkedInSelect(tree, "#positionId"), "value")
	}
	if lastPositionID.MatchString(id) {
		metadata["position_id"] = strings.ToLower(id)
	}
	for key, selector := range map[string]string{"source_id": "#sourcePid", "recruitment_type": "#recruitmentType"} {
		if value, _ := lastDetailAttribute(linkedInSelect(tree, selector), "value"); value != "" {
			metadata[key] = value
		}
	}
	if len(metadata) != 0 {
		out["metadata"] = metadata
	}
	out["language"] = "zh"
	return out, nil
}

func PhuketDetailIdentity(source string) (string, string) {
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || strings.ToLower(u.Hostname()) != "www.phuketall.com" || u.User != nil || u.Port() != "" || u.RawQuery != "" || u.Fragment != "" {
		return "", ""
	}
	m := lastPhuketPath.FindStringSubmatch(u.EscapedPath())
	if m == nil {
		return "", ""
	}
	return m[1], m[2]
}

func PhuketDocumentIdentity(source string) (string, string) {
	tree, err := finalDetailTree(source)
	if err != nil {
		return "", ""
	}
	markers := inlineSelectedNodes(tree, `meta[property="og:url"]`)
	employer, job := "", ""
	for _, n := range markers {
		value, present := lastDetailAttribute(n, "content")
		e, j := PhuketDetailIdentity(value)
		if !present || e == "" || employer != "" && (employer != e || job != j) {
			return "", ""
		}
		employer, job = e, j
	}
	return employer, job
}

func lastPhuketDate(value string) any {
	m := lastThaiDate.FindStringSubmatch(inlineTrim(value))
	if m != nil && lastThaiMonths[m[2]] != 0 {
		day, okDay, _ := talentBrewInteger(m[1])
		year, okYear, _ := talentBrewInteger(m[3])
		if !okDay || !okYear {
			return nil
		}
		if year < 100 {
			year += 1957
		} else {
			year -= 543
		}
		d := time.Date(year, time.Month(lastThaiMonths[m[2]]), day, 0, 0, 0, 0, time.UTC)
		if year < 1 || year > 9999 || d.Year() != year || int(d.Month()) != lastThaiMonths[m[2]] || d.Day() != day {
			return nil
		}
		return d.Format("2006-01-02")
	}
	for _, layout := range []string{"2 Jan 06", "2 January 2006"} {
		if d, err := time.Parse(layout, inlineNormalized(value)); err == nil {
			return d.Format("2006-01-02")
		}
	}
	return nil
}

func ParsePhuketDetail(source string) (map[string]any, error) {
	tree, err := finalDetailTree(source)
	if err != nil {
		return nil, err
	}
	out := emptyStaticDetail()
	out["title"] = staticNullable(inlineNormalized(finalNodeText(linkedInSelect(tree, ".title-feedbox h2 span"))))
	var selected *html.Node
	longest := 0
	for _, n := range inlineSelectedNodes(tree, ".jobs-derails-content > .col-md-12") {
		text := inlineNodeText(n)
		if utf8.RuneCountInString(inlineNormalized(text)) >= 20 && utf8.RuneCountInString(text) > longest {
			selected, longest = n, utf8.RuneCountInString(text)
		}
	}
	if selected != nil {
		inner, err := dom.InnerHTML(selected)
		if err != nil {
			return nil, err
		}
		if inner != "" {
			out["description"] = "<p>" + inlineTrim(inner) + "</p>"
		}
	}
	pattern, _ := dom.CompileURLPattern(`(?i)(?:ภูเก็ต|Phuket).*\b\d{5}\b`)
	for _, n := range inlineSelectedNodes(tree, ".jobs-derails-content p") {
		text := finalSeparatedText(n)
		if match, _ := pattern.FindStringMatch(text); match != nil {
			out["locations"] = []string{text}
			break
		}
	}
	details := map[string]string{}
	nodes := inlineSelectedNodes(tree, ".detailsbox > div")
	for i := 0; i+1 < len(nodes); i += 2 {
		key, value := strings.TrimRight(finalSeparatedText(nodes[i]), ":："), finalSeparatedText(nodes[i+1])
		if key != "" && value != "" {
			details[key] = value
		}
	}
	field := func(keys ...string) string {
		for _, key := range keys {
			if details[key] != "" {
				return details[key]
			}
		}
		return ""
	}
	if employment := map[string]string{"งานประจำ": "full_time", "full time": "full_time", "full-time": "full_time", "งานพาร์ทไทม์": "part_time", "part time": "part_time", "part-time": "part_time"}[strings.ToLower(field("เวลาทำงาน", "Working Time"))]; employment != "" {
		out["employment_type"] = employment
	}
	out["date_posted"] = lastPhuketDate(field("ลงประกาศเมื่อ", "Post Date"))
	metadata := map[string]any{}
	for key, labels := range map[string][]string{"department": {"แผนก", "Department"}, "quantity": {"จำนวน", "Quantity"}, "degree": {"ระดับการศึกษา", "Degree"}} {
		if value := field(labels...); value != "" {
			metadata[key] = value
		}
	}
	if len(metadata) != 0 {
		out["metadata"] = metadata
	}
	out["language"] = "en"
	return out, nil
}
