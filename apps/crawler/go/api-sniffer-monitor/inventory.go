package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"net/http"
	"net/url"
	"reflect"
	"strconv"
	"strings"
)

var ErrInventory = errors.New("configured API inventory failed")

type Job struct {
	URL             string         `json:"url"`
	SourceIdentity  string         `json:"source_identity,omitempty"`
	Title           any            `json:"title"`
	Description     any            `json:"description"`
	EmploymentType  any            `json:"employment_type"`
	JobLocationType any            `json:"job_location_type"`
	DatePosted      any            `json:"date_posted"`
	Locations       []string       `json:"locations"`
	Metadata        map[string]any `json:"metadata"`
	Extras          map[string]any `json:"extras"`
}
type Inventory struct {
	Jobs              []Job
	Truncated         bool
	LastResponseProbe bool
	URLOnly           bool
}
type Request struct {
	Method, URL, Body string
	Headers           http.Header
	Probe             bool
}
type Fetch func(context.Context, Request) (*Document, error)
type JoinURL func(string, string) (string, error)

func (d *Document) items(path string, values bool, strict ...bool) ([]map[string]any, error) {
	v, err := Search(d.Value, path)
	if err != nil {
		return nil, ErrInventory
	}
	if values {
		if obj, ok := v.(map[string]any); ok {
			a := []any{}
			for _, k := range d.order[reflect.ValueOf(obj)] {
				a = append(a, obj[k])
			}
			v = a
		}
	}
	a, ok := v.([]any)
	if !ok {
		return []map[string]any{}, nil
	}
	out := []map[string]any{}
	for _, v := range a {
		if obj, ok := v.(map[string]any); ok {
			out = append(out, obj)
		} else if len(strict) > 0 && strict[0] {
			return nil, ErrInventory
		}
	}
	return out, nil
}

func countNumber(v any) (int, bool) {
	if b, ok := v.(bool); ok {
		if b {
			return 1, true
		}
		return 0, true
	}
	n, ok := v.(json.Number)
	if !ok {
		return 0, false
	}
	f, err := n.Float64()
	if err != nil || math.IsInf(f, 0) || math.IsNaN(f) || f < 0 || f > 2000000000 {
		return 0, false
	}
	return int(f), true
}

func countKey(key string) bool {
	k := strings.ToLower(key)
	switch k {
	case "total", "count", "totalcount", "total_count", "totalresults", "total_results", "totalitems", "total_items", "hits", "numfound", "num_found", "resultcount", "result_count", "size", "nbhits":
		return true
	}
	if strings.HasPrefix(k, "total") && k != "totalpage" && k != "totalpages" {
		tail := key[5:]
		if tail == "" {
			return false
		}
		for _, r := range tail {
			if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z') {
				return false
			}
		}
		return true
	}
	return false
}

func (d *Document) total(path, explicit string) (int, bool) {
	if explicit != "" {
		v, err := Search(d.Value, explicit)
		if err != nil {
			return 0, false
		}
		return countNumber(v)
	}
	parent := ""
	if at := strings.LastIndexByte(path, '.'); at >= 0 {
		parent = path[:at]
	}
	obj, _ := Search(d.Value, parent)
	for _, v := range []any{obj, d.Value} {
		if m, ok := v.(map[string]any); ok {
			for _, k := range d.order[reflect.ValueOf(m)] {
				if countKey(k) {
					if n, ok := countNumber(m[k]); ok {
						return n, true
					}
				}
			}
		}
	}
	return 0, false
}

func requestAt(o Options, p *Pagination, value int) (Request, error) {
	r := Request{Method: o.Method, URL: o.Endpoint, Body: o.Body, Headers: o.Headers.Clone()}
	var param any = value
	if p.ValueTemplate != "" {
		s, err := formatTemplate(p.ValueTemplate, map[string]string{"value": strconv.Itoa(value)}, true)
		if err != nil {
			return r, err
		}
		param = s
	}
	if p.Location == "body" {
		s, err := setBodyParam(r.Body, p.Param, param)
		if err != nil {
			return r, err
		}
		r.Body = s
	} else {
		u, err := url.Parse(r.URL)
		if err != nil {
			return r, ErrOptions
		}
		q := u.Query()
		q.Set(p.Param, fmt.Sprint(param))
		u.RawQuery = q.Encode()
		r.URL = u.String()
	}
	return r, nil
}

var sizeParams = []string{"result_limit", "limit", "pageSize", "page_size", "size", "per_page", "perPage", "count", "rows", "hitsPerPage", "num", "rpp", "resultsPerPage", "results_per_page", "itemsPerPage", "items_per_page", "maxResults", "max_results"}

func sizeParam(o Options) (string, string, int) {
	u, _ := url.Parse(o.Endpoint)
	q := u.Query()
	for _, key := range sizeParams {
		if n, err := strconv.Atoi(q.Get(key)); err == nil {
			return key, "query", n
		}
	}
	if d, err := Decode([]byte(o.Body)); err == nil {
		if obj, ok := d.Value.(map[string]any); ok {
			for _, key := range sizeParams {
				if n, ok := countNumber(obj[key]); ok {
					return key, "body", n
				}
			}
		}
	}
	return "", "", 0
}

// Discover retains the existing first page, size probe, page/offset/cumulative
// pagination and strict whole-inventory failure. It never persists a partial
// result after a transient later-page error; caps suppress disappearance.
func Discover(ctx context.Context, o Options, fetch Fetch, join JoinURL) (Inventory, error) {
	result := Inventory{Jobs: []Job{}}
	if fetch == nil || join == nil {
		return result, ErrOptions
	}
	if o.PostDataRefresh != nil {
		source, err := fetch(ctx, Request{Method: http.MethodGet, URL: o.PostDataRefresh.source})
		if err != nil {
			return result, err
		}
		if source == nil {
			return result, ErrInventory
		}
		html, ok := source.Value.(string)
		if !ok {
			return result, ErrInventory
		}
		o, err = o.refreshedPostData(html)
		if err != nil {
			return result, err
		}
	}
	first, err := fetch(ctx, Request{Method: o.Method, URL: o.Endpoint, Body: o.Body, Headers: o.Headers.Clone()})
	if err != nil {
		return result, err
	}
	return discoverInitial(ctx, o, first, fetch, join)
}

func discoverInitial(ctx context.Context, o Options, first *Document, fetch Fetch, join JoinURL) (Inventory, error) {
	result := Inventory{Jobs: []Job{}}
	if first == nil {
		result.URLOnly = o.HTML
		if len(o.EmptyResponse) > 0 || o.AutoPath {
			return result, ErrInventory
		}
		return result, nil
	}
	first = decryptInitialResponse(first, o.ResponseDecrypt)
	if o.Convergence != nil {
		return discoverConverged(ctx, o, first, fetch, join)
	}
	if o.HTML {
		return discoverHTML(ctx, o, first, fetch, join)
	}
	if o.AutoPath {
		selected, err := first.SelectAPIArray(o.Endpoint)
		if err != nil {
			return result, err
		}
		if selected != nil {
			o.Path = selected.Path
		} else if _, array := first.Value.([]any); !array {
			// A nonempty wrapper can hide a changed or too-small inventory.
			// Only an exact configured empty response may prove absence here.
			matches, err := first.MatchesEmptyResponse(o.EmptyResponse)
			if len(o.EmptyResponse) == 0 || err != nil || !matches {
				return result, ErrInventory
			}
			return result, nil
		}
	}
	items, err := first.items(o.Path, o.PathValues, o.ItemFilter != nil && len(o.ItemFilter.RequireRegex) > 0)
	if err != nil {
		return result, err
	}
	if len(items) == 0 && len(o.EmptyResponse) > 0 {
		matches, err := first.MatchesEmptyResponse(o.EmptyResponse)
		if err != nil || !matches {
			return result, ErrInventory
		}
	}
	root := first
	total, hasTotal := first.total(o.Path, o.TotalPath)
	pageSize := len(items)
	projected := []Job{}
	sourceRows := []inventorySourceRow{}
	add := func(d *Document, rows []map[string]any) error {
		d.Root = root.Value
		for _, row := range rows {
			if o.collectRows != nil || o.ItemFilter != nil || o.AutoFields || o.AutoURLField {
				sourceRows = append(sourceRows, inventorySourceRow{d, row})
				continue
			}
			job, found, err := project(d, row, o, join)
			if err != nil {
				return err
			}
			if found {
				projected = append(projected, job)
			}
		}
		return nil
	}
	if err := add(first, items); err != nil {
		return result, err
	}
	itemCount := len(items)
	if pg := o.Pagination; pg != nil && pageSize > 0 {
		if pg.Style == "cumulative_limit" {
			if !hasTotal || total <= 0 {
				return result, ErrOptions
			}
			target := min(total, pageSize*pg.MaxPages)
			if target > pageSize {
				r, err := requestAt(o, pg, target)
				if err != nil {
					return result, err
				}
				page, err := fetch(ctx, r)
				if err != nil {
					return result, err
				}
				if page != nil {
					rows, err := page.items(o.Path, false, o.ItemFilter != nil && len(o.ItemFilter.RequireRegex) > 0)
					if err != nil {
						return result, err
					}
					if len(rows) > 0 {
						projected = nil
						sourceRows = nil
						itemCount = len(rows)
						if err := add(page, rows); err != nil {
							return result, err
						}
					}
				}
			}
		} else {
			paging := o
			increment := pg.Increment
			if name, location, size := sizeParam(o); name != "" && size < 100 {
				probe := o
				if location == "query" {
					u, _ := url.Parse(probe.Endpoint)
					q := u.Query()
					q.Set(name, "100")
					u.RawQuery = q.Encode()
					probe.Endpoint = u.String()
				} else {
					probe.Body, err = setBodyParam(probe.Body, name, 100)
					if err != nil {
						return result, err
					}
				}
				r, err := requestAt(probe, pg, pg.Start)
				if err != nil {
					return result, err
				}
				r.Probe = true
				page, probeErr := fetch(ctx, r)
				if ctx.Err() != nil {
					return result, ctx.Err()
				}
				if probeErr == nil && page != nil {
					rows, err := page.items(o.Path, false, o.ItemFilter != nil && len(o.ItemFilter.RequireRegex) > 0)
					if err != nil {
						return result, err
					}
					if len(rows) > pageSize {
						pageSize = len(rows)
						itemCount = pageSize
						projected = nil
						sourceRows = nil
						if err := add(page, rows); err != nil {
							return result, err
						}
						paging = probe
						paging.Endpoint = r.URL
						paging.Body = r.Body
						result.LastResponseProbe = true
						if n, ok := page.total(o.Path, ""); ok && n > pageSize {
							total, hasTotal = n, true
						}
						if pg.Style == "offset" {
							increment = pageSize
						}
					}
				}
			}
			pages := pg.MaxPages
			if hasTotal {
				pages = min(max(1, (total+pageSize-1)/pageSize), pages)
			}
			value := pg.Start + increment
			empty := 0
			for fetched := 1; fetched < pages; fetched++ {
				if ctx.Err() != nil {
					return result, ctx.Err()
				}
				r, err := requestAt(paging, pg, value)
				if err != nil {
					return result, err
				}
				page, err := fetch(ctx, r)
				result.LastResponseProbe = false
				if err != nil {
					return result, err
				}
				rows := []map[string]any{}
				if page != nil {
					rows, err = page.items(o.Path, false, o.ItemFilter != nil && len(o.ItemFilter.RequireRegex) > 0)
					if err != nil {
						return result, err
					}
				}
				if len(rows) == 0 {
					empty++
					if empty >= 2 {
						break
					}
				} else {
					empty = 0
					itemCount += len(rows)
					if itemCount > 2000000 {
						return result, ErrInventory
					}
					if err := add(page, rows); err != nil {
						return result, err
					}
					if len(rows) < pageSize && (!hasTotal || total == 0 || itemCount >= total) {
						break
					}
				}
				value += increment
			}
		}
	}
	if o.collectRows != nil {
		o.collectRows(sourceRows)
		return result, nil
	}
	if o.ItemFilter != nil || o.AutoFields || o.AutoURLField {
		rows := []map[string]any{}
		for _, source := range sourceRows {
			rows = append(rows, source.row)
		}
		selected := []int{}
		for i := range rows {
			selected = append(selected, i)
		}
		var err error
		if o.ItemFilter != nil {
			selected, err = FilterItemIndices(rows, o.ItemFilter)
		}
		if err != nil {
			return result, err
		}
		removed := len(sourceRows) - len(selected)
		if hasTotal {
			total = max(0, total-removed)
		}
		itemCount -= removed
		if o.AutoURLField {
			samples := []map[string]any{}
			for _, i := range selected {
				samples = append(samples, sourceRows[i].row)
				if len(samples) == 5 {
					break
				}
			}
			if len(samples) > 0 {
				o.URLField = sourceRows[selected[0]].document.FindURLField(samples)
			}
		}
		if o.AutoFields {
			samples := []inventorySourceRow{}
			for _, i := range selected {
				samples = append(samples, sourceRows[i])
				if len(samples) == 5 {
					break
				}
			}
			o.Fields, err = autoMapFields(samples)
			if err != nil {
				return result, err
			}
			result.URLOnly = len(o.Fields) == 0
		}
		for _, i := range selected {
			source := sourceRows[i]
			job, found, err := project(source.document, source.row, o, join)
			if err != nil {
				return result, err
			}
			if found {
				projected = append(projected, job)
			}
		}
	}
	unique := map[string]bool{}
	for _, job := range projected {
		unique[job.URL] = true
	}
	gap := hasTotal && total > 0 && total-len(unique) > max(1, int(math.Ceil(float64(total)*0.01)))
	result.Jobs = projected
	result.Truncated = itemCount > o.MaxItems || gap
	return result, nil
}

func project(d *Document, row map[string]any, o Options, join JoinURL) (Job, bool, error) {
	job := Job{Metadata: map[string]any{}, Extras: map[string]any{}}
	rawURL := ""
	if o.URLTemplate != "" {
		values := map[string]string{}
		for k, v := range row {
			switch v.(type) {
			case string, json.Number, bool:
				s, err := d.String(v)
				if err != nil {
					return job, false, err
				}
				values[k] = s
			}
		}
		for k, spec := range o.TemplateFields {
			v, err := d.Field(row, spec)
			if err != nil {
				return job, false, err
			}
			if s, ok := v.(string); ok {
				values[k] = s
			}
		}
		parts := []string{}
		for _, path := range o.SlugFields {
			v, err := d.Field(row, path)
			if err != nil {
				return job, false, err
			}
			if v == nil {
				continue
			}
			text, err := d.String(v)
			if err != nil {
				return job, false, err
			}
			if slug := nextdataSlug(text); slug != "" {
				parts = append(parts, slug)
			}
		}
		if len(parts) > 0 {
			values["slug"] = strings.Join(parts, "-")
		}
		rawURL, _ = formatTemplate(o.URLTemplate, values, true)
		if len(o.Fields) == 0 && rawURL != "" {
			var err error
			rawURL, err = join(o.BoardURL, rawURL)
			if err != nil {
				return job, false, err
			}
		}
	}
	if rawURL == "" && o.URLField != "" {
		var v any
		var err error
		if strings.ContainsAny(o.URLField, ".[") {
			v, err = d.Field(row, o.URLField)
		} else {
			v = row[o.URLField]
		}
		if err != nil {
			return job, false, err
		}
		if s, ok := v.(string); ok && s != "" {
			rawURL, err = join(o.BoardURL, s)
			if err != nil {
				return job, false, err
			}
		}
	}
	if rawURL == "" && len(o.Fields) > 0 {
		for _, k := range d.order[reflect.ValueOf(row)] {
			if s, ok := row[k].(string); ok && (strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://")) {
				rawURL = s
				break
			}
		}
	}
	if rawURL == "" {
		return job, false, nil
	}
	job.URL = rawURL
	for target, spec := range o.Fields {
		var value any
		var err error
		if a, ok := spec.([]any); ok {
			parts := []string{}
			for _, s := range a {
				v, err := d.Field(row, s)
				if err != nil {
					return job, false, err
				}
				if v == nil {
					continue
				}
				if text, ok := v.(string); ok {
					parts = append(parts, text)
				} else if values, ok := v.([]any); ok {
					texts := []string{}
					for _, x := range values {
						t, ok := x.(string)
						if !ok {
							return job, false, ErrField
						}
						texts = append(texts, t)
					}
					parts = append(parts, strings.Join(texts, " "))
				} else {
					return job, false, ErrField
				}
			}
			if len(parts) > 0 {
				value = strings.Join(parts, "\n\n")
			}
		} else {
			value, err = d.Field(row, spec)
			if err != nil {
				return job, false, err
			}
		}
		if value == nil {
			continue
		}
		if target == "description" {
			if a, ok := value.([]any); ok {
				parts := []string{}
				for _, x := range a {
					s, ok := x.(string)
					if !ok {
						return job, false, ErrField
					}
					if s != "" {
						parts = append(parts, s)
					}
				}
				value = strings.Join(parts, "\n\n")
				if value == "" {
					continue
				}
			}
		}
		switch target {
		case "title":
			job.Title = value
		case "description":
			job.Description = value
		case "employment_type":
			job.EmploymentType = value
		case "job_location_type":
			job.JobLocationType = value
		case "date_posted":
			job.DatePosted = value
		case "locations":
			a, ok := value.([]any)
			if !ok {
				a = []any{value}
			}
			for _, x := range a {
				s, ok := x.(string)
				if !ok {
					return job, false, ErrField
				}
				job.Locations = append(job.Locations, s)
			}
		case "skills", "responsibilities", "qualifications":
			if _, ok := value.([]any); !ok {
				value = []any{value}
			}
			job.Extras[target] = value
		case "valid_through":
			job.Extras[target] = value
		case "base_salary": // Canonical rich writes derive salary from description, as Python does.
		default:
			job.Metadata[strings.TrimPrefix(target, "metadata.")] = value
		}
	}
	return job, true, nil
}
