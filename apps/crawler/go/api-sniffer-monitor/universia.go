package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type UniversiaScope struct{ BoardID, Language string }

func ParseUniversiaScope(d *Document, o TenthProviderOptions) (UniversiaScope, error) {
	s := UniversiaScope{}
	m, ok := d.Value.(map[string]any)
	if !ok || m["slug"] != o.Slug {
		return s, ErrInventory
	}
	entity, ok := m["entity"].(map[string]any)
	if !ok {
		return s, ErrInventory
	}
	id, ok := entity["id"].(string)
	if !ok || !tenthUUID.MatchString(id) || entity["entityType"] != "company" && entity["entityType"] != "university" {
		return s, ErrInventory
	}
	s.BoardID = strings.ToLower(id)
	if o.BoardID != "" && o.BoardID != s.BoardID {
		return s, ErrInventory
	}
	if languages, ok := m["languages"].([]any); ok && len(languages) > 0 {
		if language, ok := languages[0].(string); ok && regexp.MustCompile(`^[a-z]{2}(?:-[A-Z]{2})?$`).MatchString(language) {
			s.Language = language[:2]
		}
	}
	if o.Language != "" {
		s.Language = o.Language
	}
	return s, nil
}

func UniversiaPageRequest(s UniversiaScope, offset int) Request {
	v := url.Values{"offset": {strconv.Itoa(offset)}, "limit": {"100"}, "filterAddressCountry": {"false"}, "postingType": {"job", "internship"}, "dateFrom": {""}, "boards": {s.BoardID}}
	return Request{Method: "GET", URL: "https://api-manager.universia.net/orientacion-job-posting/v1/api/job-posting?" + v.Encode(), Headers: http.Header{"Accept": {"application/json"}}}
}

func ParseUniversiaPage(d *Document, s UniversiaScope, requestedOffset int) ([]map[string]any, int, error) {
	m, ok := d.Value.(map[string]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	raw, ok := m["results"].([]any)
	if !ok {
		return nil, 0, ErrInventory
	}
	n := map[string]int{}
	for _, key := range []string{"offset", "limit", "size", "total", "totalPages"} {
		if _, ok := m[key].(json.Number); !ok {
			return nil, 0, ErrInventory
		}
		v, err := tenthNonnegative(m[key])
		if err != nil {
			return nil, 0, err
		}
		n[key] = v
	}
	expected := n["total"] - requestedOffset
	if expected < 0 {
		expected = 0
	}
	if expected > 100 {
		expected = 100
	}
	pages := n["total"] / 100
	if n["total"]%100 != 0 {
		pages++
	}
	if n["offset"] != requestedOffset || n["limit"] != 100 || n["size"] != len(raw) || n["totalPages"] != pages || len(raw) != expected {
		return nil, 0, ErrInventory
	}
	rows := []map[string]any{}
	seen := map[string]bool{}
	for _, value := range raw {
		row, ok := value.(map[string]any)
		if !ok {
			return nil, 0, ErrInventory
		}
		id, ok := row["identifier"].(string)
		if !ok || !tenthUUID.MatchString(id) || row["status"] != "published" || seen[strings.ToLower(id)] {
			return nil, 0, ErrInventory
		}
		boards, ok := row["boards"].([]any)
		bound := false
		if ok {
			for _, v := range boards {
				if strings.ToLower(tenthPythonString(d, v)) == s.BoardID {
					bound = true
					break
				}
			}
		}
		if !bound {
			return nil, 0, ErrInventory
		}
		seen[strings.ToLower(id)] = true
		rows = append(rows, row)
	}
	return rows, n["total"], nil
}

func UniversiaJobURL(raw any, id, board string) (string, error) {
	source, ok := raw.(string)
	if !ok {
		return "", ErrInventory
	}
	u, err := url.Parse(source)
	if err != nil || u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "www.universia.net") || u.User != nil || u.Port() != "" && u.Port() != "443" || u.Fragment != "" {
		return "", ErrInventory
	}
	p, err := regexp.Compile(`(?i)^/[a-z]{2}/empleo/` + regexp.QuoteMeta(id) + `/[a-z0-9][a-z0-9-]*\.html$`)
	if err != nil || !p.MatchString(u.EscapedPath()) {
		return "", ErrInventory
	}
	q, err := url.ParseQuery(u.RawQuery)
	if err != nil || strings.ToLower(q.Get("referer")) != board {
		return "", ErrInventory
	}
	for key, values := range q {
		if len(values) != 1 || key != "referer" && key != "entityid" || key == "entityid" && strings.ToLower(values[0]) != board {
			return "", ErrInventory
		}
	}
	return source, nil
}

// UniversiaJobFields retains raw enum values for the existing canonical rich
// processor, which preserves internship signals before normalizing employment.
func UniversiaJobFields(row map[string]any, s UniversiaScope) (map[string]any, error) {
	id, ok := row["identifier"].(string)
	if !ok || !tenthUUID.MatchString(id) {
		return nil, ErrInventory
	}
	id = strings.ToLower(id)
	clean := func(v any) string { text, _ := v.(string); return strings.Join(strings.Fields(text), " ") }
	title := clean(row["title"])
	description, ok := row["description"].(string)
	if title == "" || !ok || strings.TrimSpace(description) == "" {
		return nil, ErrInventory
	}
	description = strings.TrimSpace(description)
	source, err := UniversiaJobURL(row["url"], id, s.BoardID)
	if err != nil {
		return nil, err
	}
	extras := map[string]any{}
	for _, section := range []struct{ key, label string }{{"requirements", "Requirements"}, {"incentiveCompensation", "Education"}} {
		if text, ok := row[section.key].(string); ok && strings.TrimSpace(text) != "" {
			text = strings.TrimSpace(text)
			description += "<h3>" + section.label + "</h3>" + text
			previous, _ := extras["qualifications"].(string)
			extras["qualifications"] = previous + text
		}
	}
	if value := clean(row["validThrough"]); value != "" {
		extras["valid_through"] = value
	}
	metadata := map[string]any{"universia_job_id": id, "universia_board_id": s.BoardID}
	for source, target := range map[string]string{"postingType": "posting_type", "educationalLevel": "educational_level", "totalJobOpenings": "total_job_openings"} {
		if value, exists := row[source]; exists && value != nil {
			metadata[target] = value
		}
	}
	for source, target := range map[string]string{"role": "role", "contractType": "contract_type"} {
		if m, ok := row[source].(map[string]any); ok {
			if value := clean(m["name"]); value != "" {
				metadata[target] = value
			}
		}
	}
	f := map[string]any{"url": source, "title": title, "description": description, "metadata": metadata, "source_identity": "universia:" + s.BoardID + ":" + id}
	if len(extras) > 0 {
		f["extras"] = extras
	}
	if s.Language != "" {
		f["language"] = s.Language
	}
	for source, target := range map[string]string{"employmentType": "employment_type", "jobLocationType": "job_location_type", "datePosted": "date_posted"} {
		if text := clean(row[source]); text != "" {
			f[target] = text
		}
	}
	if loc, ok := row["jobLocation"].(map[string]any); ok {
		if address, ok := loc["address"].(map[string]any); ok {
			if street := clean(address["streetAddress"]); street != "" {
				f["locations"] = []string{street}
			} else {
				parts := []string{}
				seen := map[string]bool{}
				for _, key := range []string{"addressLocality", "addressRegion", "addressCountry"} {
					text := clean(address[key])
					if text != "" && !seen[text] {
						seen[text] = true
						parts = append(parts, text)
					}
				}
				if len(parts) > 0 {
					f["locations"] = []string{strings.Join(parts, ", ")}
				}
			}
		}
	}
	return f, nil
}

func DiscoverUniversia(ctx context.Context, o TenthProviderOptions, fetch TenthProviderFetch) ([]map[string]any, bool, error) {
	body, err := fetch(ctx, Request{Method: "GET", URL: o.ListingURL() + "?status=published", Headers: http.Header{"Accept": {"application/json"}}})
	if err != nil {
		return nil, false, err
	}
	d, err := Decode(body)
	if err != nil {
		return nil, false, err
	}
	s, err := ParseUniversiaScope(d, o)
	if err != nil {
		return nil, false, err
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	total := -1
	target := 0
	for offset := 0; offset == 0 || offset < target; offset += 100 {
		body, err := fetch(ctx, UniversiaPageRequest(s, offset))
		if err != nil {
			return nil, false, err
		}
		d, err := Decode(body)
		if err != nil {
			return nil, false, err
		}
		rows, count, err := ParseUniversiaPage(d, s, offset)
		if err != nil {
			return nil, false, err
		}
		if total == -1 {
			total = count
			target = total
			if target > 50000 {
				target = 50000
			}
		} else if total != count {
			return nil, false, ErrInventory
		}
		for _, row := range rows {
			id := strings.ToLower(row["identifier"].(string))
			if seen[id] {
				return nil, false, ErrInventory
			}
			seen[id] = true
			if len(jobs) < target {
				fields, err := UniversiaJobFields(row, s)
				if err != nil {
					return nil, false, err
				}
				jobs = append(jobs, fields)
			}
		}
	}
	if len(jobs) != target {
		return nil, false, ErrInventory
	}
	return jobs, total > 50000, nil
}
