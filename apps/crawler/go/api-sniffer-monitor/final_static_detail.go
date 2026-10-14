package apisniffer

import (
	_ "embed"
	"encoding/json"
	"regexp"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/net/html"
)

//go:embed final_static_detail_rules.json
var finalStaticRulesJSON []byte
var finalStaticRules struct {
	LocationTypes map[string]string `json:"location_types"`
	InternSignals []string          `json:"intern_signals"`
}

func init() {
	if json.Unmarshal(finalStaticRulesJSON, &finalStaticRules) != nil {
		panic("invalid frozen provider enum rules")
	}
}

var paycorLabel = regexp.MustCompile(`(?i)^(?:Position|Location|Job Id|# of Openings):\s*`)
var recruiterWorkplace = regexp.MustCompile(`(?i)remote|hybrid|on[ -]?site`)
var veryeastTitle = regexp.MustCompile(`^职位\s*[:：]\s*`)
var finalHeading = regexp.MustCompile(`^h[1-6]$`)
var veryeastDates = regexp.MustCompile(`\d{4}-\d{2}-\d{2}`)

func finalDetailTree(source string) (*html.Node, error) {
	if len(source) > 25_000_000 {
		return nil, ErrInventory
	}
	return html.Parse(strings.NewReader(source))
}
func finalDetailInner(n *html.Node) (any, error) {
	if n == nil {
		return nil, nil
	}
	s, e := dom.InnerHTML(n)
	if e != nil {
		return nil, e
	}
	return staticNullable(inlineTrim(s)), nil
}
func finalNodeText(n *html.Node) string {
	if n == nil {
		return ""
	}
	var b strings.Builder
	var walk func(*html.Node)
	walk = func(n *html.Node) {
		if n.Type == html.TextNode {
			b.WriteString(inlineTrim(n.Data))
		}
		for c := n.FirstChild; c != nil; c = c.NextSibling {
			walk(c)
		}
	}
	walk(n)
	return inlineTrim(b.String())
}
func finalSeparatedText(n *html.Node) string {
	if n == nil {
		return ""
	}
	return inlineNormalized(inlineNodeText(n))
}
func ParsePaycorDetail(source string) (map[string]any, error) {
	tree, e := finalDetailTree(source)
	if e != nil {
		return nil, e
	}
	out := emptyStaticDetail()
	field := func(s string) string {
		return inlineTrim(paycorLabel.ReplaceAllString(finalSeparatedText(linkedInSelect(tree, s)), ""))
	}
	out["title"] = staticNullable(field("#gnewtonJobPosition"))
	out["description"], e = finalDetailInner(linkedInSelect(tree, "#gnewtonJobDescriptionText"))
	if e != nil {
		return nil, e
	}
	if s := field("#gnewtonJobLocationInfo"); s != "" {
		out["locations"] = []string{s}
	}
	md := map[string]any{}
	for key, selector := range map[string]string{"job_id": "#gnewtonJobID", "openings": "#gnewtonJobOpening"} {
		if s := field(selector); s != "" {
			md[key] = s
		}
	}
	if len(md) > 0 {
		out["metadata"] = md
	}
	return out, nil
}
func ParseRecruiterboxDetail(source string) (map[string]any, error) {
	tree, e := finalDetailTree(source)
	if e != nil {
		return nil, e
	}
	out := emptyStaticDetail()
	out["title"] = staticNullable(finalSeparatedText(linkedInSelect(tree, ".js-job-title")))
	out["description"], e = finalDetailInner(linkedInSelect(tree, ".jobdesciption"))
	if e != nil {
		return nil, e
	}
	parts := []string{}
	for _, selector := range []string{".meta-job-location-city", ".meta-job-location-state", ".meta-job-location-country"} {
		if s := finalSeparatedText(linkedInSelect(tree, selector)); s != "" {
			parts = append(parts, s)
		}
	}
	if len(parts) > 0 {
		out["locations"] = []string{strings.Join(parts, ", ")}
	}
	values := []string{}
	if opening := linkedInSelect(tree, ".opening-info"); opening != nil {
		for _, n := range inlineSelectedNodes(opening, "span") {
			location := false
			for _, class := range strings.Fields(linkedInAttr(n, "class")) {
				if class == "meta-job-location-city" || class == "meta-job-location-state" || class == "meta-job-location-country" {
					location = true
				}
			}
			if location {
				continue
			}
			if s := strings.Trim(finalSeparatedText(n), " |"); s != "" {
				values = append(values, s)
			}
		}
	}
	if len(values) > 0 {
		out["employment_type"] = values[0]
		for _, s := range values[1:] {
			if recruiterWorkplace.MatchString(s) {
				key := strings.ToLower(inlineTrim(s))
				v := finalStaticRules.LocationTypes[key]
				if v == "" && strings.HasSuffix(key, ")") && strings.Contains(key, " (") {
					v = finalStaticRules.LocationTypes[strings.SplitN(key, " (", 2)[0]]
				}
				out["job_location_type"] = staticNullable(v)
				break
			}
		}
	}
	return out, nil
}
func finalEscape(s string) string {
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", "\"", "&quot;", "'", "&#x27;").Replace(s)
}
func ParseVeryEastDetail(source string) (map[string]any, error) {
	tree, e := finalDetailTree(source)
	if e != nil {
		return nil, e
	}
	out := emptyStaticDetail()
	out["language"] = "zh"
	out["title"] = staticNullable(veryeastTitle.ReplaceAllString(finalNodeText(linkedInSelect(tree, "#textword > ul > h3")), ""))
	fields := map[string]string{}
	for _, n := range inlineSelectedNodes(tree, "#textword > ul > li") {
		s := finalSeparatedText(n)
		i := strings.IndexAny(s, ":：")
		if i < 0 {
			continue
		}
		key := strings.Join(strings.Fields(s[:i]), "")
		value := inlineTrim(s[i+len(string([]rune(s[i:])[0])):])
		if key != "" && value != "" {
			fields[key] = value
		}
	}
	if s := fields["工作地区"]; s != "" {
		out["locations"] = []string{s}
	}
	raw := fields["职位性质"]
	key := strings.ToLower(inlineTrim(raw))
	out["employment_type"] = staticNullable(adpEmploymentTypes[key])
	for _, signal := range finalStaticRules.InternSignals {
		if key == signal {
			out["employment_type"] = raw
			break
		}
	}
	facts := strings.Builder{}
	for _, key := range []string{"职位性质", "工作地区", "招聘人数", "学历", "工作经验", "薪资待遇"} {
		if value := fields[key]; value != "" {
			facts.WriteString("<li><strong>" + finalEscape(key) + ":</strong> " + finalEscape(value) + "</li>")
		}
	}
	fragments := []string{}
	if facts.Len() > 0 {
		fragments = append(fragments, "<h3>岗位信息</h3><ul>"+facts.String()+"</ul>")
	}
	if container := linkedInSelect(tree, "#textword .describe"); container != nil {
		parts := []string{}
		for n := container.FirstChild; n != nil; n = n.NextSibling {
			if n.Type != html.ElementNode || finalHeading.MatchString(n.Data) {
				continue
			}
			outer, e := dom.OuterHTML(n)
			if e != nil {
				return nil, e
			}
			if s := inlineTrim(outer); s != "" {
				parts = append(parts, s)
			}
		}
		if len(parts) > 0 {
			fragments = append(fragments, "<h3>岗位职责/职位描述</h3>"+strings.Join(parts, "\n"))
		}
	}
	out["description"] = staticNullable(strings.Join(fragments, "\n"))
	dates := veryeastDates.FindAllString(fields["职位有效期"], -1)
	if len(dates) > 0 {
		out["date_posted"] = dates[0]
	}
	if len(dates) > 1 {
		out["extras"] = map[string]any{"valid_through": dates[1]}
	}
	md := map[string]any{}
	for key, label := range map[string]string{"quantity": "招聘人数", "degree": "学历", "experience": "工作经验"} {
		if value := fields[label]; value != "" {
			md[key] = value
		}
	}
	if len(md) > 0 {
		out["metadata"] = md
	}
	return out, nil
}
