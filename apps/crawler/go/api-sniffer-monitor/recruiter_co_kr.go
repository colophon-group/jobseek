package apisniffer

import (
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"

	"golang.org/x/net/html"
)

type RecruiterKROptions struct {
	Slug          string
	IncludeClosed bool
}

const RecruiterKRAPI = "https://api-recruiter.recruiter.co.kr"

var recruiterKRSlug = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$`)
var recruiterKRID = regexp.MustCompile(`^[A-Za-z0-9._+-]{1,128}$`)

func RecruiterKROptionsFromMetadata(source, raw string) (RecruiterKROptions, error) {
	md, err := DecodeInlineMetadata(raw)
	if err != nil || !validURL(source) {
		return RecruiterKROptions{}, ErrOptions
	}
	for _, key := range []string{"proxy", "render", "skip_ssl", "actions"} {
		if detailTruthy(md[key]) {
			return RecruiterKROptions{}, ErrOptions
		}
	}
	if md["ssl_verify"] != nil && md["ssl_verify"] != true {
		return RecruiterKROptions{}, ErrOptions
	}
	slug, _ := md["slug"].(string)
	if !detailTruthy(md["slug"]) {
		u, _ := url.Parse(source)
		host := strings.ToLower(u.Hostname())
		if strings.HasSuffix(host, ".recruiter.co.kr") {
			slug = strings.TrimSuffix(host, ".recruiter.co.kr")
		}
	}
	if !recruiterKRSlug.MatchString(slug) || slug == "www" || slug == "api" || slug == "api-recruiter" || slug == "infra1-static" || slug == "cdn" {
		return RecruiterKROptions{}, ErrOptions
	}
	return RecruiterKROptions{slug, detailTruthy(md["include_closed"])}, nil
}
func (o RecruiterKROptions) BoardURL() string {
	return "https://" + o.Slug + ".recruiter.co.kr/career/home"
}
func (o RecruiterKROptions) ListURL() string { return RecruiterKRAPI + "/position/v1/jobflex" }
func (o RecruiterKROptions) DetailURL(id string) string {
	return RecruiterKRAPI + "/position/v2/jobflex/" + id
}
func (o RecruiterKROptions) JobURL(id string) string {
	return "https://" + o.Slug + ".recruiter.co.kr/career/jobs/" + id
}
func (o RecruiterKROptions) ResourceMatches(source string) bool {
	if source == o.ListURL() {
		return true
	}
	prefix := RecruiterKRAPI + "/position/v2/jobflex/"
	return strings.HasPrefix(source, prefix) && recruiterKRID.MatchString(strings.TrimPrefix(source, prefix))
}
func (o RecruiterKROptions) Headers() http.Header {
	return http.Header{"Prefix": []string{o.Slug + ".recruiter.co.kr"}, "Accept": []string{"application/json, text/plain, */*"}, "Content-Type": []string{"application/json"}, "Referer": []string{"https://" + o.Slug + ".recruiter.co.kr/"}}
}
func (o RecruiterKROptions) ListPayload(page int) ([]byte, error) {
	if page < 1 || page > 200 {
		return nil, ErrOptions
	}
	submission, open := []string{"IN_SUBMISSION"}, []string{"OPEN"}
	if o.IncludeClosed {
		submission = []string{}
		open = []string{}
	}
	return json.Marshal(map[string]any{"pageableRq": map[string]any{"page": page, "size": 100, "sort": []string{"CREATED_DATE_TIME"}}, "filter": map[string]any{"keyword": "", "tagSnList": []any{}, "jobGroupSnList": []any{}, "careerTypeList": []any{}, "regionSnList": []any{}, "submissionStatusList": submission, "openStatusList": open, "resumeLanguageTypeList": []any{}}})
}
func RecruiterKRDate(value any) any {
	s, ok := value.(string)
	if !ok {
		return nil
	}
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	if !strings.Contains(s, "T") {
		return s
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04Z07:00", "2006-01-02T15Z07:00", "2006-01-02T15:04:05.999999999", "2006-01-02T15:04", "2006-01-02T15"} {
		zone := time.FixedZone("Asia/Seoul", 9*3600)
		parsed, err := time.ParseInLocation(layout, s, zone)
		if err == nil {
			return parsed.UTC().Format("2006-01-02")
		}
	}
	first, _, _ := strings.Cut(s, "T")
	if first == "" {
		return nil
	}
	return first
}
func (d *Document) RecruiterKRSummary(raw any, o RecruiterKROptions) (map[string]any, error) {
	row, ok := raw.(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	if row["positionSn"] == nil {
		return nil, nil
	}
	id, err := d.String(row["positionSn"])
	if err != nil || !recruiterKRID.MatchString(id) {
		return nil, ErrInventory
	}
	tags := row["tagList"]
	if !detailTruthy(tags) {
		tags = []any{}
	}
	return map[string]any{"positionSn": row["positionSn"], "url": o.JobURL(id), "list_title": row["title"], "startDateTime": row["startDateTime"], "careerType": row["careerType"], "classificationCode": row["classificationCode"], "tagList": tags, "openStatus": row["openStatus"], "submissionStatus": row["submissionStatus"]}, nil
}
func recruiterKRNodeText(n *html.Node) string {
	parts := []string{}
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			parts = append(parts, inlineTrim(n.Data))
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	visit(n)
	return inlineNormalized(strings.Join(parts, ""))
}
func recruiterKRLocations(detail, summary map[string]any) any {
	out := []string{}
	seen := map[string]bool{}
	var push func(any)
	push = func(value any) {
		switch v := value.(type) {
		case string:
			s := strings.TrimSpace(v)
			if s != "" && !seen[s] {
				seen[s] = true
				out = append(out, s)
			}
		case map[string]any:
			for _, key := range []string{"regionName", "name", "cityName", "siteName", "displayName"} {
				if value, ok := v[key].(string); ok {
					push(value)
					return
				}
			}
		}
	}
	for _, source := range []map[string]any{summary, detail} {
		for _, key := range []string{"regionList", "regionNameList", "workPlace", "workPlaceList", "workingArea", "workingAreaList", "siteList", "locationList"} {
			value := source[key]
			if rows, ok := value.([]any); ok {
				for _, v := range rows {
					push(v)
				}
			} else if detailTruthy(value) {
				push(value)
			}
		}
		for _, key := range []string{"regionName", "workPlaceName", "siteName", "cityName"} {
			push(source[key])
		}
	}
	if description, ok := detail["jobDescription"].(string); ok && strings.TrimSpace(description) != "" {
		tree, err := html.Parse(strings.NewReader(description))
		if err == nil {
			labels := map[string]bool{"근무 지역": true, "근무지역": true, "근무 장소": true, "근무장소": true, "근무지": true}
			for _, row := range inlineSelectedNodes(tree, "tr") {
				cells := inlineSelectedNodes(row, "th, td")
				for i := 0; i+1 < len(cells); i++ {
					label := recruiterKRNodeText(cells[i])
					if labels[label] {
						push(recruiterKRNodeText(cells[i+1]))
					}
				}
			}
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
func (d *Document) RecruiterKRFields(detail, summary map[string]any) (map[string]any, error) {
	first := func(values ...any) any {
		for _, v := range values {
			if detailTruthy(v) {
				return v
			}
		}
		return nil
	}
	title := first(detail["title"], summary["list_title"])
	if title == nil {
		return nil, nil
	}
	description := detail["jobDescription"]
	if kind := detail["jobDescriptionType"]; kind != nil && kind != "HTML" && detailTruthy(description) {
		s, err := d.String(description)
		if err != nil {
			return nil, err
		}
		description = "<pre>" + s + "</pre>"
	}
	tags, _ := first(detail["tagList"], summary["tagList"], []any{}).([]any)
	tagNames := []any{}
	for _, value := range tags {
		if row, ok := value.(map[string]any); ok && detailTruthy(row["tagName"]) {
			tagNames = append(tagNames, row["tagName"])
		}
	}
	md := map[string]any{}
	if v := first(detail["classificationCode"], summary["classificationCode"]); v != nil {
		md["classification"] = v
	}
	if len(tagNames) > 0 {
		md["tags"] = tagNames
	}
	if v := RecruiterKRDate(detail["endDateTime"]); v != nil {
		md["valid_through"] = v
	}
	for key, target := range map[string]string{"announcementType": "announcement_type", "recruitmentType": "recruitment_type"} {
		if v := detail[key]; detailTruthy(v) {
			md[target] = v
		}
	}
	var metadata any
	if len(md) > 0 {
		metadata = md
	}
	return map[string]any{"url": summary["url"], "title": title, "description": description, "locations": recruiterKRLocations(detail, summary), "employment_type": first(detail["careerType"], summary["careerType"]), "date_posted": RecruiterKRDate(first(detail["startDateTime"], summary["startDateTime"])), "job_location_type": nil, "language": "ko", "metadata": metadata, "extras": nil}, nil
}
