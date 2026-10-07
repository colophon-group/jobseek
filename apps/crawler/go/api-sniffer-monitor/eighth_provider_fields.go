package apisniffer

import (
	"bytes"
	"encoding/json"
	"encoding/xml"
	"html"
	"io"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/andybalholm/cascadia"
	xhtml "golang.org/x/net/html"
)

func ParseEArcuFeed(body []byte, feedURL string) ([]map[string]any, error) {
	if len(body) > 50<<20 {
		return nil, ErrInventory
	}
	// DefusedXML refuses entity declarations. Namespace-bearing elements are
	// unsupported rather than being interpreted as namespace-free inventory.
	scanner := xml.NewDecoder(bytes.NewReader(body))
	for {
		token, err := scanner.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, ErrInventory
		}
		switch token := token.(type) {
		case xml.StartElement:
			if token.Name.Space != "" {
				return nil, ErrInventory
			}
		case xml.Directive:
			if strings.Contains(strings.ToUpper(string(token)), "<!ENTITY") {
				return nil, ErrInventory
			}
		}
	}
	var feed struct {
		XMLName   xml.Name
		Positions []struct {
			URL         string   `xml:"DescriptionURL"`
			Title       string   `xml:"JobTitle"`
			Description string   `xml:"Description"`
			Date        string   `xml:"LastPublishedDate"`
			Locations   []string `xml:"Locations>Location"`
			Reference   string   `xml:"VacancyRef"`
			Function    string   `xml:"JobFunction"`
			Brand       string   `xml:"Brand"`
			Salary      string   `xml:"DisplaySalaryDescription"`
		} `xml:"position"`
	}
	decoder := xml.NewDecoder(bytes.NewReader(body))
	if decoder.Decode(&feed) != nil || strings.ToLower(feed.XMLName.Local) != "positions" || feed.XMLName.Space != "" || len(feed.Positions) > 50000 {
		return nil, ErrInventory
	}
	// A second document or non-whitespace trailer cannot silently become zero.
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, ErrInventory
		}
		switch token := token.(type) {
		case xml.CharData:
			if strings.TrimSpace(string(token)) != "" {
				return nil, ErrInventory
			}
		case xml.Comment, xml.ProcInst:
		default:
			return nil, ErrInventory
		}
	}
	base, err := url.Parse(feedURL)
	if err != nil || base.Scheme != "https" || base.User != nil || base.RawQuery != "" || base.Fragment != "" || !strings.HasSuffix(strings.ToLower(base.Path), "/allvacancies/") {
		return nil, ErrInventory
	}
	prefix := base.Path[:len(base.Path)-len("/allvacancies/")] + "/vacancy/"
	out := []map[string]any{}
	seen := map[string]bool{}
	for _, row := range feed.Positions {
		title, raw := strings.TrimSpace(row.Title), strings.TrimSpace(row.URL)
		ref, err := url.Parse(raw)
		if err != nil || title == "" || raw == "" {
			return nil, ErrInventory
		}
		u := base.ResolveReference(ref)
		if u.Scheme != "https" || u.User != nil || !strings.EqualFold(u.Hostname(), base.Hostname()) || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" || !strings.HasPrefix(strings.ToLower(u.Path), strings.ToLower(prefix)) {
			return nil, ErrInventory
		}
		u.Host = strings.ToLower(base.Hostname())
		source := u.String()
		if seen[source] {
			return nil, ErrInventory
		}
		seen[source] = true
		locations := []string{}
		for _, value := range row.Locations {
			if value = strings.TrimSpace(value); value != "" {
				locations = append(locations, value)
			}
		}
		metadata := map[string]any{}
		for key, value := range map[string]string{"reference": row.Reference, "job_function": row.Function, "brand": row.Brand, "salary_description": row.Salary} {
			if value = strings.TrimSpace(value); value != "" {
				metadata[key] = value
			}
		}
		fields := map[string]any{"url": source, "title": title}
		if len(locations) != 0 {
			fields["locations"] = locations
		}
		if len(metadata) != 0 {
			fields["metadata"] = metadata
		}
		if value := strings.TrimSpace(row.Description); value != "" {
			fields["description"] = value
		}
		if value := strings.TrimSpace(row.Date); value != "" {
			fields["date_posted"] = value
		}
		out = append(out, fields)
	}
	return out, nil
}

func eighthTree(body []byte) (*xhtml.Node, error) {
	if len(body) > 30000000 {
		return nil, ErrInventory
	}
	return xhtml.Parse(strings.NewReader(string(body)))
}

func eighthSelect(tree *xhtml.Node, selector string) []*xhtml.Node {
	compiled, err := cascadia.Compile(selector)
	if err != nil {
		return nil
	}
	return cascadia.QueryAll(tree, compiled)
}

func eighthAttr(node *xhtml.Node, key string) string {
	value, _ := inlineAttribute(node, key)
	return value
}

func eighthText(node *xhtml.Node) string {
	if node == nil {
		return ""
	}
	return strings.Join(strings.Fields(paylocityText(node, " ")), " ")
}

func eighthFirst(tree *xhtml.Node, selector string) *xhtml.Node {
	values := eighthSelect(tree, selector)
	if len(values) == 0 {
		return nil
	}
	return values[0]
}

var eighthDigits = regexp.MustCompile(`\d[\d., ]*`)

func CVWarehouseSection(body []byte) (string, int, error) {
	tree, err := eighthTree(body)
	if err != nil {
		return "", 0, err
	}
	section, count := "", -1
	for _, link := range eighthSelect(tree, `a[href*="section="]`) {
		u, err := url.Parse(eighthAttr(link, "href"))
		if err != nil || !eighthSection.MatchString(u.Query().Get("section")) {
			continue
		}
		number := eighthDigits.FindString(eighthText(eighthFirst(link, ".badge")))
		digits := regexp.MustCompile(`[^0-9]`).ReplaceAllString(number, "")
		var n int
		if digits == "" || json.Unmarshal([]byte(digits), &n) != nil {
			continue
		}
		candidate := u.Query().Get("section")
		if n > count || n == count && candidate > section {
			section, count = candidate, n
		}
	}
	if section == "" {
		return "", 0, ErrInventory
	}
	return section, count, nil
}

func CVWarehouseLocales(body []byte, source string) ([]string, error) {
	tree, err := eighthTree(body)
	base, parseErr := url.Parse(source)
	if err != nil || parseErr != nil {
		return nil, ErrInventory
	}
	urls, seen := []string{source}, map[string]bool{source: true}
	for _, link := range eighthSelect(tree, "#language-modal a[href]") {
		u, err := url.Parse(eighthAttr(link, "href"))
		if err != nil {
			continue
		}
		u = base.ResolveReference(u)
		if u.Hostname() != base.Hostname() || !eighthSection.MatchString(u.Query().Get("section")) || seen[u.String()] {
			continue
		}
		urls = append(urls, u.String())
		seen[u.String()] = true
		if len(urls) == 20 {
			break
		}
	}
	return urls, nil
}

func ParseCVWarehousePage(body []byte, source string) ([]map[string]any, error) {
	tree, err := eighthTree(body)
	base, parseErr := url.Parse(source)
	if err != nil || parseErr != nil {
		return nil, ErrInventory
	}
	details := map[string]*xhtml.Node{}
	for _, node := range eighthSelect(tree, "[data-jobdetail-job-id]") {
		if id := eighthAttr(node, "data-jobdetail-job-id"); id != "" {
			details[id] = node
		}
	}
	out, seen := []map[string]any{}, map[string]bool{}
	for _, card := range eighthSelect(tree, "[data-item-collection]") {
		link := eighthFirst(card, "[data-jobid][href]")
		if link == nil {
			continue
		}
		id := eighthAttr(link, "data-jobid")
		if id == "" || strings.Trim(id, "0123456789") != "" || seen[id] {
			continue
		}
		seen[id] = true
		detail := details[id]
		if detail == nil {
			return nil, ErrInventory
		}
		title, location := eighthText(eighthFirst(detail, "h2.job-title")), eighthText(eighthFirst(detail, ".additional-data .location"))
		descriptionNode := eighthFirst(detail, ".jobDescriptionText")
		var description strings.Builder
		if descriptionNode != nil {
			for node := descriptionNode.FirstChild; node != nil; node = node.NextSibling {
				if xhtml.Render(&description, node) != nil {
					return nil, ErrInventory
				}
			}
		}
		if title == "" || location == "" || strings.TrimSpace(description.String()) == "" {
			return nil, ErrInventory
		}
		u, err := url.Parse(eighthAttr(link, "href"))
		if err != nil {
			return nil, ErrInventory
		}
		u = base.ResolveReference(u)
		if u.Hostname() != base.Hostname() || u.Query().Get("job") != id || u.User != nil || u.Scheme != "https" || u.Port() != "" && u.Port() != "443" {
			return nil, ErrInventory
		}
		language := strings.ToLower(strings.Split(u.Query().Get("lang"), "-")[0])
		u.RawQuery, u.Fragment = url.Values{"job": {id}}.Encode(), ""
		fields := map[string]any{"url": u.String(), "title": title, "description": strings.TrimSpace(description.String()), "locations": []string{location}}
		if language != "" {
			fields["language"] = language
		}
		metadata := map[string]any{"job_id": id}
		workType := eighthAttr(card, "data-filter-worktype")
		if workType != "" {
			metadata["work_type"] = workType
		}
		var schedule []any
		scheduleDoc, scheduleErr := Decode([]byte(eighthAttr(card, "data-filter-workschedule")))
		if scheduleErr == nil {
			schedule, _ = scheduleDoc.Value.([]any)
		}
		values := []string{}
		for _, item := range schedule {
			value, err := scheduleDoc.String(item)
			if err != nil {
				return nil, err
			}
			value = strings.TrimSpace(value)
			if value != "" {
				values = append(values, value)
			}
		}
		if len(values) != 0 {
			metadata["work_schedule"] = values
			fields["employment_type"] = values[0]
		}
		if workType == "Stagiair" {
			fields["employment_type"] = "internship"
		}
		if brand := eighthAttr(card, "data-filter-attribute"); brand != "" {
			var parsed any
			if json.Unmarshal([]byte(brand), &parsed) != nil {
				parsed = brand
			}
			metadata["brand"] = parsed
		}
		for _, node := range eighthSelect(card, ".workType") {
			if eighthFirst(node, ".lni-laptop") != nil {
				if value := eighthText(node); value != "" {
					fields["job_location_type"] = value
				}
				break
			}
		}
		fields["metadata"] = metadata
		out = append(out, fields)
	}
	return out, nil
}

var eighthDatePrefix = regexp.MustCompile(`^(\d{4}-\d{2}-\d{2})(?:$|[ T])`)
var eighthHTMLTag = regexp.MustCompile(`<[^>]+>`)
var woowaLocation = regexp.MustCompile(`(?:근무\s*지역|모집\s*지역|근무지|근무\s*장소)\s*[:：]\s*(.{1,100}?)(?:\s+(?:역할|고용\s*형태|계약\s*기간|구분|\[조직\s*소개\])|$)`)

func eighthDate(value any) (any, error) {
	s, _ := value.(string)
	s = strings.TrimSpace(s)
	if s == "" || strings.HasPrefix(s, "2999-") || strings.HasPrefix(s, "9999-") {
		return nil, nil
	}
	m := eighthDatePrefix.FindStringSubmatch(s)
	if len(m) != 2 {
		return nil, ErrInventory
	}
	if _, err := time.Parse("2006-01-02", m[1]); err != nil {
		return nil, ErrInventory
	}
	return m[1], nil
}

func woowaEmployment(detail map[string]any) string {
	employment, _ := detail["employmentType"].(map[string]any)
	code, _ := employment["recruitItemCode"].(string)
	return map[string]string{"BA002001": "full_time", "BA002002": "temporary", "BA002003": "internship", "BA002004": "temporary"}[code]
}

func WoowaJobFields(o EighthProviderOptions, listing, detail map[string]any) (map[string]any, error) {
	id, _ := detail["recruitNumber"].(string)
	if !woowaRecruitID.MatchString(id) || listing["recruitNumber"] != id {
		return nil, ErrInventory
	}
	value := detail["recruitName"]
	if !detailTruthy(value) {
		value = listing["recruitName"]
	}
	title, ok := value.(string)
	if !ok || strings.TrimSpace(title) == "" {
		return nil, ErrInventory
	}
	description, _ := detail["recruitContents"].(string)
	locations := []string{}
	if o.Variant == "bmart" {
		branches := detail["desiredBranches"]
		if !detailTruthy(branches) {
			branches = listing["desiredBranches"]
		}
		rows, _ := branches.([]any)
		for _, raw := range rows {
			row, _ := raw.(map[string]any)
			remark, _ := row["recruitItemRemark"].(string)
			q, _ := url.ParseQuery(remark)
			candidate := q.Get("addr")
			if candidate == "" {
				candidate, _ = row["recruitItemName"].(string)
			}
			if candidate = strings.TrimSpace(candidate); candidate != "" {
				locations = append(locations, candidate+", South Korea")
			}
		}
		if len(locations) == 0 {
			if summary, ok := listing["description"].(string); ok && strings.TrimSpace(summary) != "" {
				locations = []string{strings.TrimSpace(summary) + ", South Korea"}
			}
		}
	} else {
		if strings.TrimSpace(description) == "" {
			return nil, ErrInventory
		}
		visible := strings.Join(strings.Fields(html.UnescapeString(eighthHTMLTag.ReplaceAllString(description, " "))), " ")
		if match := woowaLocation.FindStringSubmatch(visible); len(match) == 2 {
			raw := strings.Trim(match[1], " ,.;")
			if !strings.HasPrefix(raw, "전국") {
				for _, part := range regexp.MustCompile(`\s*(?:&|/|·)\s*`).Split(raw, -1) {
					part = strings.Trim(part, " ()")
					if part == "" {
						continue
					}
					if translated := map[string]string{"서울": "Seoul", "인천": "Incheon", "대전": "Daejeon", "광주": "Gwangju", "대구": "Daegu", "부산": "Busan", "울산": "Ulsan"}[part]; translated != "" {
						part = translated
					}
					locations = append(locations, part+", South Korea")
				}
			}
		}
	}
	if len(locations) == 0 {
		locations = []string{"South Korea"}
	}
	employment := woowaEmployment(detail)
	if o.Variant == "bmart" {
		description = "<h3>" + eighthEscape(title) + "</h3><p>Work location: " + eighthEscape(strings.Join(locations, "; ")) + "</p>"
		if employment != "" {
			description += "<p>Employment type: " + eighthEscape(employment) + "</p>"
		}
		items := ""
		checklist, _ := detail["applicantCheckList"].([]any)
		for _, raw := range checklist {
			row, _ := raw.(map[string]any)
			name, _ := row["recruitItemName"].(string)
			remark, _ := row["recruitItemRemark"].(string)
			if name = strings.TrimSpace(name); name != "" {
				if remark = strings.TrimSpace(remark); remark != "" {
					name += " " + remark
				}
				items += "<li>" + eighthEscape(name) + "</li>"
			}
		}
		if items != "" {
			description += "<h3>Applicant information</h3><ul>" + items + "</ul>"
		}
	}
	posted, err := eighthDate(detail["recruitOpenDate"])
	if err != nil {
		return nil, err
	}
	through, err := eighthDate(detail["recruitEndDate"])
	if err != nil {
		return nil, err
	}
	path := "/recruitment/" + id + "/detail"
	if o.Variant == "bmart" {
		path = "/recruitment/detail/" + id
	}
	metadata := map[string]any{"recruit_number": id}
	for key, field := range map[string]string{"corporation": "recruitCorporationNumber", "career_type": "careerType", "job_group": "jobGroup"} {
		if v := detail[field]; v != nil {
			metadata[key] = v
		}
	}
	fields := map[string]any{"url": o.Origin + path, "title": strings.TrimSpace(title), "description": description, "locations": locations, "language": "ko", "metadata": metadata, "source_identity": "woowa:" + o.Variant + ":" + id}
	if posted != nil {
		fields["date_posted"] = posted
	}
	if through != nil {
		fields["extras"] = map[string]any{"valid_through": through}
	}
	if employment != "" {
		fields["employment_type"] = employment
	}
	return fields, nil
}

func eighthEscape(value string) string {
	return strings.NewReplacer("&#34;", "&quot;", "&#39;", "&#x27;").Replace(html.EscapeString(value))
}
