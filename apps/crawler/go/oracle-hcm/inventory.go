package oraclehcm

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/url"
	"strconv"
	"strings"
)

type Job struct {
	URL                               string
	Title, DatePosted, EmploymentType any
	Locations                         []string
}
type Inventory struct {
	Jobs      []Job
	Truncated bool
}
type Fetch func(context.Context, string) ([]byte, error)
type partition struct {
	parameter, id string
	count         int
	cached        map[string]any
}

func wrapper(body []byte) (map[string]any, error) {
	if len(body) > 64<<20 {
		return nil, ErrInventory
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	var root map[string]any
	if decoder.Decode(&root) != nil || decoder.Decode(new(any)) != io.EOF {
		return nil, ErrInventory
	}
	items, ok := root["items"].([]any)
	if !ok || len(items) == 0 {
		return nil, ErrInventory
	}
	w, ok := items[0].(map[string]any)
	if !ok {
		return nil, ErrInventory
	}
	return w, nil
}

func organizationApplied(w map[string]any, organization string, total int) bool {
	if organization == "" {
		return true
	}
	selected, ok := facetID(w["SelectedOrganizationsFacet"])
	if !ok || selected != organization {
		return false
	}
	rows, ok := w["organizationsFacet"].([]any)
	if !ok {
		return false
	}
	matches := 0
	for _, value := range rows {
		row, ok := value.(map[string]any)
		if !ok {
			continue
		}
		id, ok := facetID(row["Id"])
		if !ok || id != organization {
			continue
		}
		n, valid := integer(row["TotalCount"])
		if !valid || n != total {
			return false
		}
		matches++
	}
	return matches == 1
}

func partitions(w map[string]any, total int) []partition {
	for _, facet := range [][2]string{{"categoriesFacet", "selectedCategoriesFacet"}, {"organizationsFacet", "selectedOrganizationsFacet"}} {
		rows, ok := w[facet[0]].([]any)
		if !ok || len(rows) == 0 {
			continue
		}
		out := []partition{}
		seen := map[string]bool{}
		sum := 0
		valid := true
		for _, value := range rows {
			row, ok := value.(map[string]any)
			if !ok {
				valid = false
				break
			}
			id, ok := facetID(row["Id"])
			n, nok := integer(row["TotalCount"])
			if !ok || !nok || n <= 0 || n > 10000 || seen[id] || sum > total-n {
				valid = false
				break
			}
			sum += n
			seen[id] = true
			out = append(out, partition{parameter: facet[1], id: id, count: n})
		}
		if valid && sum == total {
			return out
		}
	}
	return nil
}

// Discover follows Oracle's compound finder offsets and proven facet partitions.
// A later request failure supplies no complete inventory. Moving totals, short
// pages, duplicate IDs and caps retain their explicit truncation evidence.
func Discover(ctx context.Context, o Options, fetch Fetch) (Inventory, error) {
	empty := Inventory{Jobs: []Job{}}
	if fetch == nil || !hostPattern.MatchString(o.Host) || !sitePattern.MatchString(o.Site) || o.Organization != "" && !facetPattern.MatchString(o.Organization) || o.OffsetOverlap < 0 || o.OffsetOverlap >= 200 || o.TotalTolerance < 0 || o.ShortfallTolerance < 0 || o.ShortfallTolerance >= 200 || o.DuplicateTolerance < 0 {
		return empty, ErrOptions
	}
	for _, key := range []string{"title", "locations", "date_posted", "employment_type"} {
		if field := o.Fields[key]; field == "" || len(field) > 256 || strings.ContainsRune(field, 0) {
			return empty, ErrOptions
		}
	}
	read := func(source string) (map[string]any, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		body, err := fetch(ctx, source)
		if err != nil {
			return nil, err
		}
		return wrapper(body)
	}
	base := o.Endpoint()
	first, err := read(base)
	if err != nil {
		return empty, err
	}
	total, ok := integer(first["TotalJobsCount"])
	if !ok {
		return empty, ErrInventory
	}
	if total == 0 {
		return empty, nil
	}
	if !organizationApplied(first, o.Organization, total) {
		return empty, ErrInventory
	}
	sources := []partition{}
	if total > 10000 {
		sources = partitions(first, total)
	}
	if len(sources) == 0 {
		sources = []partition{{count: total, cached: first}}
	}
	result := empty
	seen := map[string]bool{}
	duplicates := 0
	minimumTotal := 0
	requests := 1
	for _, source := range sources {
		latest, minimum := source.count, source.count
		sourceSeen := map[string]bool{}
		endpoint := base
		if source.parameter != "" {
			endpoint += "," + source.parameter + "=" + url.QueryEscape(source.id)
		}
		for offset := 0; offset < latest; offset += 200 - o.OffsetOverlap {
			if offset >= 10000 || requests >= 1000 || len(seen) >= 50000 {
				result.Truncated = true
				break
			}
			var page map[string]any
			if offset == 0 && source.cached != nil {
				page = source.cached
			} else {
				request := endpoint
				if offset > 0 {
					request += ",offset=" + strconv.Itoa(offset)
				}
				page, err = read(request)
				requests++
				if err != nil {
					return empty, err
				}
			}
			if value, exists := page["TotalJobsCount"]; exists {
				count, valid := integer(value)
				if !valid {
					result.Truncated = true
				} else {
					if !organizationApplied(page, o.Organization, count) {
						return empty, ErrInventory
					}
					if count != latest {
						if o.OffsetOverlap == 0 || latest-count > o.OffsetOverlap {
							result.Truncated = true
						}
						latest = count
						minimum = min(minimum, count)
					}
				}
			} else if o.Organization != "" {
				return empty, ErrInventory
			}
			rows, valid := page["requisitionList"].([]any)
			if !valid {
				return empty, ErrInventory
			}
			shortfall := min(200, max(latest-offset, 0)) - len(rows)
			if len(rows) > 200 || shortfall > 0 && (offset+200 < latest && shortfall > o.ShortfallTolerance || offset+200 >= latest && shortfall > o.TotalTolerance) {
				result.Truncated = true
			}
			if len(rows) == 0 {
				break
			}
			pageSeen := map[string]bool{}
			for _, value := range rows {
				row, valid := value.(map[string]any)
				if !valid {
					return empty, ErrInventory
				}
				var id string
				switch raw := row["Id"].(type) {
				case string:
					id = raw
				case json.Number:
					if value, valid := facetID(raw); valid && value != "0" {
						id = value
					}
				}
				if !idPattern.MatchString(id) {
					result.Truncated = true
					continue
				}
				if pageSeen[id] {
					duplicates++
					if duplicates > o.DuplicateTolerance {
						result.Truncated = true
					}
					continue
				}
				pageSeen[id] = true
				if sourceSeen[id] {
					if o.OffsetOverlap == 0 {
						result.Truncated = true
					}
					continue
				}
				sourceSeen[id] = true
				if seen[id] {
					result.Truncated = true
					continue
				}
				seen[id] = true
				job := Job{URL: o.JobURL(id), Title: row[o.Fields["title"]], DatePosted: row[o.Fields["date_posted"]], EmploymentType: row[o.Fields["employment_type"]]}
				if raw := row[o.Fields["locations"]]; raw != nil {
					location, valid := raw.(string)
					if !valid {
						return empty, ErrInventory
					}
					if location != "" {
						job.Locations = []string{location}
					}
				}
				result.Jobs = append(result.Jobs, job)
			}
		}
		minimumTotal += minimum
	}
	if len(seen) < minimumTotal-o.TotalTolerance {
		result.Truncated = true
	}
	return result, ctx.Err()
}
