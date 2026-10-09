package apisniffer

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"strings"
)

type ByteDancePortal struct {
	Kind, Endpoint, WebsitePath, Channel, DetailTemplate string
	PortalType                                           int
	PartitionCategories                                  bool
}

func ByteDanceOptionsFromURL(raw string) (ByteDancePortal, error) {
	u, e := url.Parse(raw)
	if e != nil || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" {
		return ByteDancePortal{}, ErrOptions
	}
	p := strings.TrimRight(u.Path, "/")
	host := strings.ToLower(u.Hostname())
	if (host == "joinbytedance.com" || host == "www.joinbytedance.com") && (p == "" || p == "/search") {
		return ByteDancePortal{"global", "https://jobs.bytedance.com/api/v1/public/supplier/search/job/posts", "en", "", "https://joinbytedance.com/search/{id}", 0, false}, nil
	}
	if host == "jobs.bytedance.com" {
		if p == "/experienced/position" {
			return ByteDancePortal{"experienced", "https://jobs.bytedance.com/api/v1/search/job/posts", "society", "office", "https://jobs.bytedance.com/experienced/position/{id}/detail", 2, true}, nil
		}
		if p == "/campus/position" {
			return ByteDancePortal{"campus", "https://jobs.bytedance.com/api/v1/search/job/posts", "campus", "campus", "https://jobs.bytedance.com/campus/position/{id}/detail", 3, false}, nil
		}
	}
	return ByteDancePortal{}, ErrOptions
}

const ByteDanceFilterURL = "https://jobs.bytedance.com/api/v1/config/job/filters/2"

func (p ByteDancePortal) Headers() http.Header {
	h := http.Header{"Accept": {"application/json, text/plain, */*"}, "Content-Type": {"application/json"}, "Website-Path": {p.WebsitePath}, "Portal-Platform": {"pc"}}
	if p.Channel != "" {
		h.Set("Portal-Channel", p.Channel)
	}
	if p.Kind == "global" {
		h.Set("X-Tt-Env", "boe_epam_api")
	}
	return h
}
func (p ByteDancePortal) PageRequest(offset int, categories []string) Request {
	if categories == nil {
		categories = []string{}
	}
	m := map[string]any{"recruitment_id_list": []string{}, "job_category_id_list": categories, "subject_id_list": []string{}, "location_code_list": []string{}, "keyword": "", "limit": 1000, "offset": offset}
	if p.Kind != "global" {
		m["tag_id_list"] = []string{}
		m["portal_type"] = p.PortalType
		m["job_function_id_list"] = []string{}
		m["storefront_id_list"] = []string{}
		m["portal_entrance"] = 1
	}
	b, _ := json.Marshal(m)
	return Request{Method: "POST", URL: p.Endpoint, Body: string(b), Headers: p.Headers()}
}
func (p ByteDancePortal) RequestMatches(r Request) bool {
	return r.Method == "POST" && r.URL == p.Endpoint || p.PartitionCategories && r.Method == "GET" && r.URL == ByteDanceFilterURL && r.Body == ""
}
func byteDanceText(v any) string { s, _ := v.(string); return strings.TrimSpace(s) }
func ByteDanceJob(m map[string]any, p ByteDancePortal) (Job, error) {
	id := jobStreetScalar(m["id"])
	if id == "" || len(id) > 128 || strings.ContainsAny(id, "/?#\\\x00") {
		return Job{}, ErrInventory
	}
	j := Job{URL: strings.Replace(p.DetailTemplate, "{id}", id, 1)}
	if title := byteDanceText(m["title"]); title != "" {
		j.Title = title
	}
	description := ""
	if s := byteDanceText(m["description"]); s != "" {
		description += "<h3>Responsibilities</h3>" + s
	}
	if s := byteDanceText(m["requirement"]); s != "" {
		description += "<h3>Requirements</h3>" + s
	}
	if description != "" {
		j.Description = description
	}
	city := jobStreetMap(m["city_info"])
	parent := jobStreetMap(city["parent"])
	parts := []string{}
	for _, v := range []any{city["en_name"], parent["en_name"]} {
		if s := byteDanceText(v); s != "" && (len(parts) == 0 || parts[0] != s) {
			parts = append(parts, s)
		}
	}
	if len(parts) > 0 {
		j.Locations = []string{strings.Join(parts, ", ")}
	}
	recruit := jobStreetMap(m["recruit_type"])
	employment := byteDanceText(recruit["en_name"])
	if employment == "" {
		employment = byteDanceText(recruit["name"])
	}
	if employment != "" {
		j.EmploymentType = employment
	}
	category, ok := m["job_category"].(map[string]any)
	if !ok {
		category = jobStreetMap(m["job_type"])
	}
	team := byteDanceText(category["en_name"])
	if team == "" {
		team = byteDanceText(category["name"])
	}
	if team != "" {
		j.Metadata = map[string]any{"team": team}
	}
	if v := m["create_time"]; v != nil {
		j.DatePosted = pythonString(v)
	}
	return j, nil
}
func byteDanceEnvelope(d *Document) (map[string]any, error) {
	if d == nil {
		return nil, ErrInventory
	}
	m, ok := d.Value.(map[string]any)
	if !ok || !byteDanceSuccessCode(m["code"]) {
		return nil, ErrInventory
	}
	data, ok := m["data"].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return data, nil
}
func ByteDancePartitions(d *Document) ([][]string, error) {
	m, e := byteDanceEnvelope(d)
	if e != nil {
		return nil, e
	}
	categories, ok := m["job_type_list"].([]any)
	counts, valid := m["job_type_count_map"].(map[string]any)
	if !ok || !valid || len(categories) == 0 || len(categories) > 10000 {
		return nil, ErrInventory
	}
	out := [][]string{}
	for _, v := range categories {
		row, ok := v.(map[string]any)
		id, valid := row["id"].(string)
		if !ok || !valid || len(id) > 128 {
			return nil, ErrInventory
		}
		n, valid := byteDanceCount(counts[id])
		if !valid {
			return nil, ErrInventory
		}
		if n == 0 {
			continue
		}
		if n < 10000 {
			out = append(out, []string{id})
			continue
		}
		children, ok := row["children"].([]any)
		if !ok || len(children) == 0 {
			return nil, ErrInventory
		}
		for _, v := range children {
			child, ok := v.(map[string]any)
			id, valid := child["id"].(string)
			if !ok || !valid || len(id) > 128 {
				return nil, ErrInventory
			}
			out = append(out, []string{id})
		}
		if len(out) > 10000 {
			return nil, ErrInventory
		}
	}
	return out, nil
}
func DiscoverByteDance(ctx context.Context, p ByteDancePortal, fetch Fetch) (Inventory, error) {
	out := Inventory{Jobs: []Job{}}
	if fetch == nil {
		return out, ErrOptions
	}
	partitions := [][]string{nil}
	if p.PartitionCategories {
		if e := ctx.Err(); e != nil {
			return out, e
		}
		d, e := fetch(ctx, Request{Method: "GET", URL: ByteDanceFilterURL, Headers: p.Headers()})
		if e != nil {
			return out, e
		}
		partitions, e = ByteDancePartitions(d)
		if e != nil {
			return out, e
		}
	}
	all := []map[string]any{}
	global := map[string]bool{}
	for _, categories := range partitions {
		seen := map[string]bool{}
		expected := -1
		items := []map[string]any{}
		for offset := 0; expected < 0 || offset < expected; offset += 1000 {
			if e := ctx.Err(); e != nil {
				return Inventory{}, e
			}
			d, e := fetch(ctx, p.PageRequest(offset, categories))
			if e != nil {
				return Inventory{}, e
			}
			m, e := byteDanceEnvelope(d)
			if e != nil {
				return Inventory{}, e
			}
			rows, ok := m["job_post_list"].([]any)
			total, valid := byteDanceCount(m["count"])
			if !ok || !valid || total >= 10000 || expected >= 0 && total != expected || len(rows) > 1000 {
				return Inventory{}, ErrInventory
			}
			expected = total
			if total == 0 {
				if len(rows) > 0 {
					return Inventory{}, ErrInventory
				}
				break
			}
			if len(rows) == 0 {
				return Inventory{}, ErrInventory
			}
			for _, v := range rows {
				row, ok := v.(map[string]any)
				if !ok {
					return Inventory{}, ErrInventory
				}
				id := jobStreetScalar(row["id"])
				if id == "" || seen[id] {
					return Inventory{}, ErrInventory
				}
				seen[id] = true
				items = append(items, row)
			}
		}
		if len(seen) != expected {
			return Inventory{}, ErrInventory
		}
		for _, m := range items {
			id := jobStreetScalar(m["id"])
			if global[id] {
				return Inventory{}, ErrInventory
			}
			global[id] = true
			all = append(all, m)
		}
		if len(all) > 1000000 {
			return Inventory{}, ErrInventory
		}
	}
	for _, m := range all {
		j, e := ByteDanceJob(m, p)
		if e != nil || j.Title == nil || j.Description == nil || len(j.Locations) == 0 {
			return Inventory{}, ErrInventory
		}
		out.Jobs = append(out.Jobs, j)
	}
	return out, nil
}

// Keep IDs and dates as the original scalar strings; no new source identity.
func pythonString(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if n, ok := v.(json.Number); ok {
		return string(n)
	}
	if b, ok := v.(bool); ok {
		if b {
			return "True"
		}
		return "False"
	}
	if v == nil {
		return "None"
	}
	b, _ := json.Marshal(v)
	return string(b)
}

func byteDanceSuccessCode(v any) bool {
	if b, ok := v.(bool); ok {
		return !b
	}
	if n, ok := v.(json.Number); ok {
		f, e := n.Float64()
		return e == nil && f == 0
	}
	return false
}
func byteDanceCount(v any) (int, bool) {
	if b, ok := v.(bool); ok {
		if b {
			return 1, true
		}
		return 0, true
	}
	if _, ok := v.(json.Number); !ok {
		return 0, false
	}
	return darwinboxInteger(v)
}
