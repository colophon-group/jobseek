package apisniffer

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/text/cases"
)

type DarwinboxBoard struct{ Host, CompanyID string }

var darwinboxCompanyID = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$`)
var darwinboxJobID = regexp.MustCompile(`^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$`)

func DarwinboxBoardFromURL(raw string) (DarwinboxBoard, error) {
	u, e := url.Parse(raw)
	if e != nil || len(raw) > 4096 || u.Scheme != "https" || u.User != nil || u.Opaque != "" || u.Port() != "" && u.Port() != "443" || u.RawQuery != "" || u.Fragment != "" {
		return DarwinboxBoard{}, ErrOptions
	}
	host := strings.TrimSuffix(strings.ToLower(u.Hostname()), ".")
	tenant := ""
	for _, suffix := range []string{".darwinbox.in", ".darwinbox.com"} {
		if strings.HasSuffix(host, suffix) {
			tenant = strings.TrimSuffix(host, suffix)
		}
	}
	if !beisenTenant.MatchString(tenant) || tenant == "api" || tenant == "app" || tenant == "help" || tenant == "static" || tenant == "support" || tenant == "www" {
		return DarwinboxBoard{}, ErrOptions
	}
	parts := []string{}
	for _, p := range strings.Split(u.Path, "/") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) < 3 || !strings.EqualFold(parts[0], "ms") {
		return DarwinboxBoard{}, ErrOptions
	}
	company := "main"
	tail := parts[2:]
	if strings.EqualFold(parts[1], "candidate") && strings.EqualFold(tail[0], "careers") {
		tail = tail[1:]
	} else if (strings.EqualFold(parts[1], "candidate") || strings.EqualFold(parts[1], "candidatev2")) && len(tail) >= 2 && strings.EqualFold(tail[1], "careers") {
		company = strings.TrimSpace(tail[0])
		tail = tail[2:]
	} else {
		return DarwinboxBoard{}, ErrOptions
	}
	if !darwinboxCompanyID.MatchString(company) || len(tail) != 0 && (len(tail) != 2 || !strings.EqualFold(tail[0], "jobDetails") || !darwinboxJobID.MatchString(tail[1])) {
		return DarwinboxBoard{}, ErrOptions
	}
	return DarwinboxBoard{host, company}, nil
}

func DarwinboxOptionsFromMetadata(board, raw string) (DarwinboxBoard, error) {
	b, e := DarwinboxBoardFromURL(board)
	if e != nil {
		return b, e
	}
	d, e := Decode([]byte(raw))
	if e != nil {
		return b, ErrOptions
	}
	m, ok := d.Value.(map[string]any)
	if !ok {
		return b, ErrOptions
	}
	for k, v := range m {
		switch k {
		case "host":
			h, ok := v.(string)
			if !ok || strings.TrimSuffix(strings.ToLower(strings.TrimSpace(h)), ".") != b.Host {
				return b, ErrOptions
			}
		case "company_id":
			c, ok := v.(string)
			if !ok || strings.TrimSpace(c) != b.CompanyID {
				return b, ErrOptions
			}
		case "wait":
			if v != "commit" && v != "domcontentloaded" && v != "load" && v != "networkidle" {
				return b, ErrOptions
			}
		case "timeout":
			n, ok := integer(v)
			if !ok || n < 1 || n > 120000 {
				return b, ErrOptions
			}
		default:
			return b, ErrOptions
		}
	}
	return b, nil
}
func (b DarwinboxBoard) ListingURL() string {
	return "https://" + b.Host + "/ms/candidatev2/" + b.CompanyID + "/careers"
}
func (b DarwinboxBoard) JobsURL() string { return "https://" + b.Host + "/ms/candidateapi/job/alljobs" }
func (b DarwinboxBoard) PageRequest(page int) Request {
	body, _ := json.Marshal(struct {
		Company string `json:"companyId"`
		Page    int    `json:"page"`
		Sort    string `json:"sort_option"`
		Limit   int    `json:"limit"`
	}{b.CompanyID, page, "new", 100})
	return Request{Method: "POST", URL: b.JobsURL(), Body: string(body), Headers: http.Header{"Accept": {"application/json"}, "Authorization": {"undefined"}, "Content-Type": {"application/json"}, "X-Requested-With": {"XMLHttpRequest"}}}
}
func darwinboxClean(v any) string { s, _ := v.(string); return strings.Join(strings.Fields(s), " ") }
func darwinboxInteger(v any) (int, bool) {
	if n, ok := v.(json.Number); ok {
		if strings.ContainsAny(string(n), ".eE") {
			return 0, false
		}
		i, e := strconv.Atoi(string(n))
		return i, e == nil && i >= 0
	}
	if s, ok := v.(string); ok && regexp.MustCompile(`^[0-9]+$`).MatchString(s) {
		i, e := strconv.Atoi(s)
		return i, e == nil && i >= 0
	}
	return 0, false
}
func DarwinboxJob(raw any, b DarwinboxBoard, normalize SmallDescriptionNormalizer) (Job, bool, error) {
	m, ok := raw.(map[string]any)
	if !ok {
		return Job{}, false, nil
	}
	id := strings.TrimSpace(jobStreetScalar(m["id"]))
	if n, ok := m["id"].(json.Number); ok && (strings.ContainsAny(string(n), ".eE") || strings.HasPrefix(string(n), "-") || n == "0") {
		id = ""
	}
	title := darwinboxClean(m["title"])
	if title == "" {
		title = darwinboxClean(m["designation_display_name"])
	}
	if !darwinboxJobID.MatchString(id) || title == "" {
		return Job{}, false, nil
	}
	job := Job{URL: b.ListingURL() + "/jobDetails/" + id, Title: title, Metadata: map[string]any{"id": id}}
	for _, pair := range [][2]string{{"internal_job_code", "job_code"}, {"department_name_only", "department"}, {"experience", "experience"}, {"salary_range", "salary_range"}} {
		if s := darwinboxClean(m[pair[0]]); s != "" {
			job.Metadata[pair[1]] = s
		}
	}
	values := []any{}
	for _, key := range []string{"tool_tip_locations", "officelocations_without_area", "officelocations_area"} {
		if a, ok := m[key].([]any); ok && len(a) > 0 {
			values = a
			break
		}
	}
	if len(values) == 0 {
		if s := darwinboxClean(m["locations"]); s != "" && !strings.EqualFold(s, "multiple locations") {
			values = []any{s}
		}
	}
	seen := map[string]bool{}
	fold := cases.Fold()
	for _, v := range values {
		if s := darwinboxClean(v); s != "" && !seen[fold.String(s)] {
			seen[fold.String(s)] = true
			job.Locations = append(job.Locations, s)
		}
	}
	if s := darwinboxClean(m["emp_type_name"]); s != "" {
		job.EmploymentType = s
	}
	remote := m["is_remote"] == true
	if n, ok := m["is_remote"].(json.Number); ok {
		f, e := n.Float64()
		remote = e == nil && f == 1
	}
	if s, ok := m["is_remote"].(string); ok {
		switch strings.ToLower(strings.TrimSpace(s)) {
		case "1", "true", "yes":
			remote = true
		}
	}
	if remote {
		job.JobLocationType = "remote"
	}
	if raw, ok := m["jd"].(string); ok && normalize != nil {
		value, e := normalize(raw)
		if e != nil {
			return Job{}, false, e
		}
		if value != nil {
			job.Description = *value
		}
	}
	date := ""
	if n, ok := m["posted_on"].(json.Number); ok {
		if f, e := n.Float64(); e == nil && f > -62135596800 && f < 253402300800000 {
			if f > 100000000000 {
				f /= 1000
			}
			if f >= -62135596800 && f < 253402300800 {
				date = time.Unix(int64(math.Floor(f)), 0).UTC().Format("2006-01-02")
			}
		}
	}
	if date == "" {
		if s, ok := m["posted_on"].(string); ok {
			if n, ok := darwinboxInteger(s); ok {
				f := int64(n)
				if f > 100000000000 {
					f /= 1000
				}
				if f < 253402300800 {
					date = time.Unix(f, 0).UTC().Format("2006-01-02")
				}
			}
		}
	}
	if date == "" {
		s := darwinboxClean(m["created_on"])
		for _, layout := range []string{time.RFC3339Nano, "2006-01-02T15:04:05", "2006-01-02 15:04:05", "2006-01-02"} {
			if t, e := time.Parse(layout, s); e == nil {
				date = t.Format("2006-01-02")
				break
			}
		}
	}
	if date != "" {
		job.DatePosted = date
	}
	return job, true, nil
}

// emit preserves each validated page for the original streaming writer. On a
// later failure the returned prefix never authorizes absent-job reconciliation.
func DiscoverDarwinbox(ctx context.Context, b DarwinboxBoard, fetch Fetch, normalize SmallDescriptionNormalizer, emit func([]Job) error) (Inventory, error) {
	out := Inventory{Jobs: []Job{}}
	if fetch == nil || normalize == nil {
		return out, ErrOptions
	}
	expected := -1
	changed := false
	rawSeen, invalid, duplicates := 0, 0, 0
	seen := map[string]bool{}
	for page := 1; page <= 500; page++ {
		if e := ctx.Err(); e != nil {
			return out, e
		}
		d, e := fetch(ctx, b.PageRequest(page))
		if e != nil {
			return out, e
		}
		parse := func(d *Document) (int, []any, error) {
			if d == nil {
				return 0, nil, ErrInventory
			}
			m, ok := d.Value.(map[string]any)
			if !ok {
				return 0, nil, ErrInventory
			}
			status, ok := m["status"].(string)
			n, valid := darwinboxInteger(m["job_counts"])
			rows, arr := m["data"].([]any)
			if !ok || !strings.EqualFold(status, "success") || !valid || !arr || len(rows) > 100 || len(rows) > n {
				return 0, nil, ErrInventory
			}
			return n, rows, nil
		}
		total, rows, e := parse(d)
		if e != nil {
			return out, e
		}
		if expected >= 0 && total != expected {
			changed = true
		}
		expected = total
		if len(rows) == 0 && rawSeen < total {
			d, e = fetch(ctx, b.PageRequest(page))
			if e != nil {
				return out, e
			}
			total, rows, e = parse(d)
			if e != nil {
				return out, e
			}
			if total != expected {
				changed = true
				expected = total
			}
			if len(rows) == 0 {
				return out, ErrInventory
			}
		}
		jobs := []Job{}
		for _, raw := range rows {
			rawSeen++
			job, valid, e := DarwinboxJob(raw, b, normalize)
			if e != nil {
				return out, e
			}
			if !valid {
				invalid++
				continue
			}
			if seen[job.URL] {
				duplicates++
				continue
			}
			seen[job.URL] = true
			jobs = append(jobs, job)
		}
		if rawSeen < total && len(rows) < 100 {
			return out, ErrInventory
		}
		done := rawSeen >= total || rawSeen >= 50000
		if done {
			out.Truncated = changed || invalid > 0 || duplicates > 0 || rawSeen != total || total > 50000
		}
		if len(jobs) > 0 || done {
			if emit != nil {
				if e := emit(jobs); e != nil {
					return out, e
				}
			}
			out.Jobs = append(out.Jobs, jobs...)
		}
		if done {
			if expected > 0 && len(seen) == 0 {
				return out, ErrInventory
			}
			return out, nil
		}
	}
	out.Truncated = true
	return out, nil
}
