package apisniffer

import (
	"encoding/json"
	"github.com/andybalholm/cascadia"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"golang.org/x/net/html"
	"net/url"
	"regexp"
	"strings"
)

type PaylocityOptions struct{ Listing string }

var paylocityPageData = regexp.MustCompile(`\bwindow\.pageData\s*=\s*`)
var paylocityDetail = regexp.MustCompile(`(?i)^/recruiting/jobs/details/([0-9]{1,32})/?$`)

func PaylocityHost(host string) bool {
	labels := strings.Split(strings.TrimRight(strings.ToLower(host), "."), ".")
	return len(labels) == 3 && labels[1] == "paylocity" && labels[2] == "com" && strings.HasSuffix(labels[0], "recruiting")
}
func PaylocityOptionsFromMetadata(source, raw string) (PaylocityOptions, error) {
	_, e := fifthDirectMetadata(source, raw)
	u, ue := url.Parse(source)
	if e != nil || ue != nil || !PaylocityHost(u.Hostname()) || !strings.Contains(strings.ToLower(u.Path), "/recruiting/jobs/") {
		return PaylocityOptions{}, ErrOptions
	}
	return PaylocityOptions{source}, nil
}
func (o PaylocityOptions) ResourceMatches(source string) bool { return source == o.Listing }
func (o PaylocityOptions) JobURL(id string) string {
	u, _ := url.Parse(o.Listing)
	return u.Scheme + "://" + u.Host + "/Recruiting/Jobs/Details/" + id
}
func PaylocityDetailRoute(source string) error {
	u, e := url.Parse(source)
	if e != nil || !validURL(source) || !PaylocityHost(u.Hostname()) || !paylocityDetail.MatchString(u.Path) || u.RawQuery != "" {
		return ErrOptions
	}
	return nil
}
func PaylocityPage(source string, o PaylocityOptions) ([]map[string]any, error) {
	marker := paylocityPageData.FindStringIndex(source)
	if marker == nil {
		return nil, ErrInventory
	}
	dec := json.NewDecoder(strings.NewReader(source[marker[1]:]))
	dec.UseNumber()
	var data map[string]any
	if dec.Decode(&data) != nil || data == nil {
		return nil, ErrInventory
	}
	rows, ok := data["Jobs"].([]any)
	if !ok {
		return nil, ErrInventory
	}
	out := []map[string]any{}
	for _, v := range rows {
		r, ok := v.(map[string]any)
		if !ok || r["JobId"] == nil {
			continue
		}
		var id string
		switch v := r["JobId"].(type) {
		case json.Number:
			id = v.String()
		case string:
			id = v
		default:
			return nil, ErrInventory
		}
		for _, c := range id {
			if c < '0' || c > '9' {
				return nil, ErrInventory
			}
		}
		if len(id) == 0 || len(id) > 32 {
			return nil, ErrInventory
		}
		md := map[string]any{"job_id": r["JobId"]}
		if department, ok := r["HiringDepartment"].(string); ok && department != "" {
			md["department"] = department
		}
		location, _ := r["LocationName"].(string)
		var locations, kind any
		if strings.TrimSpace(location) != "" {
			locations = []string{location}
		}
		if strings.Contains(strings.ToLower(location), "hybrid") {
			kind = "hybrid"
		} else if detailTruthy(r["IsRemote"]) || strings.Contains(strings.ToLower(location), "remote") {
			kind = "remote"
		}
		out = append(out, map[string]any{"url": o.JobURL(id), "title": r["JobTitle"], "locations": locations, "job_location_type": kind, "date_posted": r["PublishedDate"], "metadata": md, "description": nil, "employment_type": nil, "language": nil, "extras": nil})
	}
	return out, nil
}
func paylocityText(node *html.Node, separator string) string {
	pieces := []string{}
	var visit func(*html.Node)
	visit = func(n *html.Node) {
		if n.Type == html.TextNode {
			if s := strings.TrimSpace(n.Data); s != "" {
				pieces = append(pieces, s)
			}
		}
		for child := n.FirstChild; child != nil; child = child.NextSibling {
			visit(child)
		}
	}
	if node != nil {
		visit(node)
	}
	return strings.Join(pieces, separator)
}
func paylocityAfterHeader(tree *html.Node, label string) *html.Node {
	for _, n := range cascadia.QueryAll(tree, cascadia.MustCompile(".job-listing-header")) {
		if strings.EqualFold(paylocityText(n, ""), label) {
			for next := n.NextSibling; next != nil; next = next.NextSibling {
				if next.Type == html.ElementNode {
					return next
				}
			}
			return nil
		}
	}
	return nil
}
func ParsePaylocityDetail(source string) (map[string]any, error) {
	tree, e := html.Parse(strings.NewReader(source))
	if e != nil {
		return nil, e
	}
	var title, description, employment, locations, kind any
	if s := paylocityText(cascadia.Query(tree, cascadia.MustCompile(".job-preview-title span")), ""); s != "" {
		title = s
	}
	if node := paylocityAfterHeader(tree, "Description"); node != nil {
		s, e := dom.InnerHTML(node)
		if e != nil {
			return nil, e
		}
		if s = strings.TrimSpace(s); s != "" {
			description = s
		}
	}
	if s := paylocityText(paylocityAfterHeader(tree, "Job Type"), ""); s != "" {
		employment = s
	}
	if node := cascadia.Query(tree, cascadia.MustCompile(".preview-location")); node != nil {
		if link := cascadia.Query(node, cascadia.MustCompile(`a[href*="maps.google"]`)); link != nil {
			if s := paylocityText(link, ""); s != "" {
				locations = []string{s}
			}
			kind = "onsite"
		} else {
			parts := []string{}
			for _, s := range strings.Split(paylocityText(node, "|"), "|") {
				if s = strings.TrimSpace(s); s != "" && s != "•" {
					parts = append(parts, s)
				}
			}
			if len(parts) > 0 {
				location := parts[0]
				marker := strings.ToLower(parts[0])
				if len(parts) > 1 && (marker == "fully remote" || marker == "hybrid remote" || marker == "on-site" || marker == "onsite") {
					location = parts[1]
				}
				if location != "" {
					locations = []string{location}
				}
				combined := strings.ToLower(strings.Join(parts, " "))
				kind = "onsite"
				if strings.Contains(combined, "hybrid") {
					kind = "hybrid"
				} else if strings.Contains(combined, "remote") {
					kind = "remote"
				}
			}
		}
	}
	return map[string]any{"title": title, "description": description, "locations": locations, "employment_type": employment, "job_location_type": kind, "date_posted": nil, "base_salary": nil, "language": nil, "extras": nil, "metadata": nil}, nil
}
