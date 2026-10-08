package apisniffer

import (
	"context"
	"encoding/json"
	"html"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
)

type SmallProviderFetch func(context.Context, Request) ([]byte, error)
type SmallDescriptionNormalizer func(string) (*string, error)

var smallUnsigned = regexp.MustCompile(`^[0-9]+$`)
var smallDatePattern = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}$`)
var smallParagraphBreak = regexp.MustCompile(`\n\s*\n`)

func smallInt(v any, text bool) (int, error) {
	var s string
	switch v := v.(type) {
	case json.Number:
		s = string(v)
	case string:
		if !text {
			return 0, ErrInventory
		}
		s = v
	default:
		return 0, ErrInventory
	}
	if !smallUnsigned.MatchString(s) {
		return 0, ErrInventory
	}
	n, e := strconv.Atoi(s)
	if e != nil || n < 0 || n > 1_000_000_000 {
		return 0, ErrInventory
	}
	return n, nil
}
func smallText(v any) string { s, _ := v.(string); return strings.TrimSpace(s) }
func smallDate(v any) any {
	s := smallText(v)
	s = strings.SplitN(s, " ", 2)[0]
	if s == "0000-00-00" || !smallDatePattern.MatchString(s) {
		return nil
	}
	return s
}
func smallHTML(s string) string {
	parts := smallParagraphBreak.Split(s, -1)
	out := []string{}
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		part = html.EscapeString(part)
		part = strings.ReplaceAll(part, "&#39;", "&#x27;")
		part = strings.ReplaceAll(part, "&#34;", "&quot;")
		out = append(out, "<p>"+strings.ReplaceAll(part, "\n", "<br>")+"</p>")
	}
	return strings.Join(out, "\n")
}

func DiscoverSmallProvider(ctx context.Context, o SmallProviderOptions, fetch SmallProviderFetch, normalize SmallDescriptionNormalizer) ([]map[string]any, bool, error) {
	if o.Provider == "jarvi" {
		return DiscoverJarvi(ctx, o.BoardURL, o.PublicKey, o.Currency, fetch)
	}
	if o.Provider == "job51" {
		return DiscoverJob51(ctx, o.BoardURL, o.CTMID, fetch, normalize)
	}
	jobs := []map[string]any{}
	seen := map[string]bool{}
	expected, originalPages, originalSize := -1, -1, -1
	for page := 1; page <= 10000; page++ {
		query := url.Values{}
		headers := http.Header{}
		switch o.Provider {
		case "cnstaff":
			query.Set("n", "1")
			query.Set("p", strconv.Itoa(page))
			headers.Set("Accept", "application/json, text/javascript, */*; q=0.01")
			headers.Set("Referer", o.Origin+"/recruit")
			headers.Set("X-Requested-With", "XMLHttpRequest")
		case "jobbank104":
			query.Set("page", strconv.Itoa(page))
			query.Set("pageSize", "100")
			headers.Set("Accept", "application/json")
			headers.Set("Referer", "https://www.104.com.tw/company/"+o.Tenant)
		case "seamlesshiring":
			query.Set("limit", "100")
			query.Set("page", strconv.Itoa(page))
			headers.Set("Accept", "application/json")
		default:
			return nil, false, ErrOptions
		}
		body, e := fetch(ctx, Request{Method: "GET", URL: o.ListingURL() + "?" + query.Encode(), Headers: headers})
		if e != nil {
			return nil, false, e
		}
		d, e := Decode(body)
		if e != nil {
			return nil, false, e
		}
		root, ok := d.Value.(map[string]any)
		if !ok {
			return nil, false, ErrInventory
		}
		var rows []any
		total, pages, size := 0, 0, 0
		var next any
		switch o.Provider {
		case "cnstaff":
			total, e = smallInt(root["total"], true)
			if e != nil {
				return nil, false, e
			}
			meta, ok := root["page"].(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			current, e := smallInt(meta["now"], true)
			if e != nil || current != page {
				return nil, false, ErrInventory
			}
			pages, e = smallInt(meta["total"], true)
			if e != nil {
				return nil, false, e
			}
			size = 15
			rows, ok = root["list"].([]any)
			if !ok {
				return nil, false, ErrInventory
			}
		case "jobbank104":
			data, ok := root["data"].(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			total, e = smallInt(data["totalCount"], false)
			if e != nil {
				return nil, false, e
			}
			pages, e = smallInt(data["totalPages"], false)
			if e != nil {
				return nil, false, e
			}
			size, e = smallInt(data["pageSize"], false)
			if e != nil || size < 1 || size > 100 {
				return nil, false, ErrInventory
			}
			current, e := smallInt(data["page"], false)
			if e != nil || current != page {
				return nil, false, ErrInventory
			}
			list, ok := data["list"].(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			rows, ok = list["normalJobs"].([]any)
			if !ok {
				return nil, false, ErrInventory
			}
		case "seamlesshiring":
			data, ok := root["data"].(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			listing, ok := data["jobs"].(map[string]any)
			if !ok {
				return nil, false, ErrInventory
			}
			total, e = smallInt(listing["total"], false)
			if e != nil {
				return nil, false, e
			}
			rows, ok = listing["data"].([]any)
			if !ok {
				return nil, false, ErrInventory
			}
			next = listing["next_page_url"]
		}
		if expected != -1 && total != expected {
			return nil, false, ErrInventory
		}
		expected = total
		if o.Provider != "seamlesshiring" {
			if pages != (total+size-1)/size || len(rows) != min(size, max(0, total-(page-1)*size)) || originalPages != -1 && (pages != originalPages || size != originalSize) {
				return nil, false, ErrInventory
			}
			originalPages, originalSize = pages, size
		}
		before := len(jobs)
		for _, value := range rows {
			row, ok := value.(map[string]any)
			if !ok {
				if o.Provider == "seamlesshiring" {
					continue
				}
				return nil, false, ErrInventory
			}
			fields, id, e := smallJobFields(o, d, row, normalize)
			if e != nil {
				return nil, false, e
			}
			if fields == nil {
				continue
			}
			if seen[id] {
				if o.Provider == "seamlesshiring" {
					continue
				}
				return nil, false, ErrInventory
			}
			seen[id] = true
			jobs = append(jobs, fields)
			if len(jobs) >= 50000 && o.Provider != "jobbank104" {
				break
			}
		}
		if o.Provider == "seamlesshiring" {
			if !detailTruthy(next) || len(jobs) >= 50000 {
				if total < len(jobs) {
					return nil, false, ErrInventory
				}
				return jobs, total > len(jobs) || len(jobs) >= 50000, nil
			}
			if len(jobs) == before {
				return nil, false, ErrInventory
			}
		} else if len(jobs) >= min(total, 50000) || page >= pages {
			if len(jobs) != min(total, 50000) {
				return nil, false, ErrInventory
			}
			return jobs, total > 50000, nil
		}
	}
	return nil, false, ErrInventory
}

func smallJobFields(o SmallProviderOptions, d *Document, row map[string]any, normalize SmallDescriptionNormalizer) (map[string]any, string, error) {
	fields := map[string]any{}
	switch o.Provider {
	case "jobbank104":
		id := strings.ToLower(smallText(row["jobNo"]))
		source := smallText(row["jobUrl"])
		u, e := url.Parse(source)
		if e != nil || u.Scheme != "https" || !strings.EqualFold(u.Hostname(), "www.104.com.tw") || u.User != nil || u.Port() != "" && u.Port() != "443" || !jobbankToken.MatchString(id) {
			return nil, "", ErrInventory
		}
		parts := strings.FieldsFunc(u.Path, func(r rune) bool { return r == '/' })
		if len(parts) != 2 || !strings.EqualFold(parts[0], "job") || strings.ToLower(parts[1]) != id {
			return nil, "", ErrInventory
		}
		title, location, description := smallText(row["jobName"]), smallText(row["jobAddrNoDesc"]), smallText(row["jobDescription"])
		if title == "" || location == "" || description == "" {
			return nil, "", ErrInventory
		}
		fields = map[string]any{"url": "https://www.104.com.tw/job/" + id, "title": title, "description": smallHTML(description), "locations": []string{location}}
		return fields, id, nil
	case "cnstaff":
		id, title := smallText(row["job_id"]), smallText(row["job_name_show"])
		if title == "" {
			title = smallText(row["job_name"])
		}
		if !smallUnsigned.MatchString(id) || title == "" || normalize == nil {
			return nil, "", ErrInventory
		}
		responsibility, e := normalize(smallText(row["job_detail"]))
		if e != nil {
			return nil, "", e
		}
		qualification, e := normalize(smallText(row["job_desc2"]))
		if e != nil {
			return nil, "", e
		}
		parts := []string{}
		extras := map[string]any{}
		if responsibility != nil && *responsibility != "" {
			parts = append(parts, *responsibility)
			extras["responsibilities"] = *responsibility
		}
		if qualification != nil && *qualification != "" {
			parts = append(parts, *qualification)
			extras["qualifications"] = *qualification
		}
		description, e := normalize(strings.Join(parts, "\n"))
		if e != nil {
			return nil, "", e
		}
		if description == nil {
			return nil, "", ErrInventory
		}
		if expiry := smallDate(row["job_end_at"]); expiry != nil {
			extras["valid_through"] = expiry
		}
		meta := map[string]any{"id": id}
		for field, key := range map[string]string{"employer": "company_orgnize_name_show", "department": "ws_system_job_type_ids_name", "job_function": "g_job_type"} {
			if value := smallText(row[key]); value != "" {
				meta[field] = value
			}
		}
		fields = map[string]any{"url": o.Origin + "/recruitment/job/detail/id/" + id + "/", "title": title, "description": *description, "language": "zh", "metadata": meta, "date_posted": smallDate(row["job_published_at"])}
		if len(extras) > 0 {
			fields["extras"] = extras
		}
		if location := smallText(row["job_address_name"]); location != "" {
			fields["locations"] = []string{location}
		}
		return fields, id, nil
	case "seamlesshiring":
		if row["id"] == nil {
			return nil, "", nil
		}
		id, e := d.String(row["id"])
		if e != nil || id == "" || strings.ContainsAny(id, "/\\?#\x00\r\n ") {
			return nil, "", ErrInventory
		}
		meta := map[string]any{"id": row["id"]}
		for key, raw := range map[string]string{"valid_through": "expiry_date", "position": "position"} {
			if detailTruthy(row[raw]) {
				meta[key] = row[raw]
			}
		}
		fields = map[string]any{"url": o.Origin + "/job/view/" + id, "title": row["title"], "metadata": meta, "job_location_type": row["work_style"]}
		parts := []string{}
		for _, key := range []string{"summary", "details"} {
			if detailTruthy(row[key]) {
				s, e := d.String(row[key])
				if e != nil {
					return nil, "", e
				}
				if s = strings.TrimSpace(s); s != "" {
					parts = append(parts, s)
				}
			}
		}
		if len(parts) > 0 {
			fields["description"] = strings.Join(parts, "\n")
		}
		location := row["location"]
		if !detailTruthy(location) {
			location = row["city"]
		}
		if !detailTruthy(location) {
			if details, ok := row["location_details"].(map[string]any); ok {
				location = details["name"]
			}
		}
		if detailTruthy(location) {
			s, e := d.String(location)
			if e != nil {
				return nil, "", e
			}
			if s = strings.TrimSpace(s); s != "" {
				fields["locations"] = []string{s}
			}
		}
		if detailTruthy(row["job_type"]) {
			fields["employment_type"] = row["job_type"]
		}
		date := row["post_date"]
		if !detailTruthy(date) {
			date = row["created_at"]
		}
		fields["date_posted"] = date
		return fields, id, nil
	}
	return nil, "", ErrOptions
}
