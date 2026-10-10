package apisniffer

import (
	"context"
	"encoding/json"
	"strconv"
	"strings"

	"github.com/dlclark/regexp2/v2"
)

type PaginationConvergence struct {
	MaxPasses, RequiredNoGrowth int
	Identity, StableFields      []string
	RejectDuplicates            bool
	URLPattern                  *regexp2.Regexp
	URLFields                   map[string]string
}

func parsePaginationConvergence(value, match any, o Options) (*PaginationConvergence, error) {
	if value == nil {
		if match != nil {
			return nil, ErrOptions
		}
		return nil, nil
	}
	m, ok := value.(map[string]any)
	if !ok || o.Pagination == nil || o.Pagination.Style != "page" && o.Pagination.Style != "offset" || o.HTML || o.AutoPath || o.PathValues || o.TotalPath == "" || o.ResponseDecrypt != nil || o.PostDataRefresh != nil {
		return nil, ErrOptions
	}
	for k := range m {
		if k != "max_passes" && k != "required_no_growth_passes" && k != "identity_by" && k != "stable_fields" {
			return nil, ErrOptions
		}
	}
	c := &PaginationConvergence{}
	c.MaxPasses, ok = integer(m["max_passes"])
	if !ok || c.MaxPasses < 3 || c.MaxPasses > 8 {
		return nil, ErrOptions
	}
	c.RequiredNoGrowth, ok = integer(m["required_no_growth_passes"])
	if !ok || c.RequiredNoGrowth < 2 || c.RequiredNoGrowth >= c.MaxPasses {
		return nil, ErrOptions
	}
	paths := func(v any) ([]string, error) {
		values, err := itemStringList(v, 16, false)
		if err != nil {
			return nil, err
		}
		for i := range values {
			values[i], err = itemPath(values[i])
			if err != nil {
				return nil, err
			}
		}
		return values, nil
	}
	var err error
	if m["identity_by"] != nil {
		c.Identity, err = paths(m["identity_by"])
		c.RejectDuplicates = true
	} else if o.ItemFilter != nil {
		c.Identity = append([]string(nil), o.ItemFilter.DedupeBy...)
	}
	if err != nil || len(c.Identity) == 0 {
		return nil, ErrOptions
	}
	if m["stable_fields"] != nil {
		if !c.RejectDuplicates {
			return nil, ErrOptions
		}
		c.StableFields, err = paths(m["stable_fields"])
		if err != nil {
			return nil, err
		}
	}
	if match != nil {
		v, ok := match.(map[string]any)
		if !ok || len(v) != 2 || o.URLField == "" {
			return nil, ErrOptions
		}
		pattern, ok := v["pattern"].(string)
		if !ok || len(pattern) == 0 || len(pattern) > 4096 {
			return nil, ErrOptions
		}
		c.URLPattern, err = itemRegex(pattern, true)
		fields, ok := v["fields"].(map[string]any)
		if err != nil || !ok || len(fields) == 0 || len(fields) > 16 {
			return nil, ErrOptions
		}
		c.URLFields = map[string]string{}
		for group, raw := range fields {
			path, err := itemPath(raw)
			if group == "" || err != nil {
				return nil, ErrOptions
			}
			c.URLFields[group] = path
		}
		named := 0
		for _, group := range c.URLPattern.GetGroupNames() {
			if _, err := strconv.Atoi(group); err == nil {
				continue
			}
			if c.URLFields[group] == "" {
				return nil, ErrOptions
			}
			named++
		}
		if named != len(c.URLFields) {
			return nil, ErrOptions
		}
	}
	return c, nil
}

func convergenceShape(d *Document, o Options) (int, bool) {
	if d == nil {
		return 0, false
	}
	value, err := Search(d.Value, o.Path)
	rows, ok := value.([]any)
	if err != nil || !ok {
		return 0, false
	}
	for _, row := range rows {
		if _, ok := row.(map[string]any); !ok {
			return 0, false
		}
	}
	total, err := Search(d.Value, o.TotalPath)
	if _, numeric := total.(json.Number); err != nil || !numeric {
		return 0, false
	}
	return countNumber(total)
}

func convergenceIdentity(source inventorySourceRow, o Options) (string, string, bool) {
	c := o.Convergence
	parts := []string{}
	for _, path := range c.Identity {
		value, err := source.document.Field(source.row, path)
		text, ok := value.(string)
		if err != nil || !ok || text == "" {
			return "", "", false
		}
		parts = append(parts, text)
	}
	if c.URLPattern != nil {
		value, err := source.document.Field(source.row, o.URLField)
		url, ok := value.(string)
		if err != nil || !ok || url == "" {
			return "", "", false
		}
		match, err := c.URLPattern.FindStringMatch(url)
		if err != nil || match == nil {
			return "", "", false
		}
		for group, path := range c.URLFields {
			value, err := source.document.Field(source.row, path)
			text, ok := value.(string)
			if err != nil || !ok || text == "" || match.GroupByName(group).String() != text {
				return "", "", false
			}
		}
	}
	var projection any = source.row
	if len(c.StableFields) > 0 {
		values := []any{}
		for _, path := range c.StableFields {
			value, err := source.document.Field(source.row, path)
			if err != nil || value == nil || value == "" {
				return "", "", false
			}
			values = append(values, value)
		}
		projection = values
	}
	id, err := json.Marshal(parts)
	stable, e := json.Marshal(projection)
	return string(id), string(stable), err == nil && e == nil
}

// Reuse the original page collector, preserving probes, request order and
// first observations. Only complete, unchanged passes can authorize delists.
func discoverConverged(ctx context.Context, o Options, first *Document, fetch Fetch, join JoinURL) (Inventory, error) {
	accumulated := []inventorySourceRow{}
	seen, projections := map[string]bool{}, map[string]string{}
	expected, initialValid := convergenceShape(first, o)
	noGrowth, proven := 0, false
	for pass := 1; pass <= o.Convergence.MaxPasses; pass++ {
		if ctx.Err() != nil {
			return Inventory{}, ctx.Err()
		}
		total, valid := convergenceShape(first, o)
		if !initialValid || !valid {
			break
		}
		valid = total == expected
		rows := []inventorySourceRow{}
		passOptions := o
		passOptions.Convergence = nil
		passOptions.collectRows = func(values []inventorySourceRow) { rows = values }
		validatingFetch := func(ctx context.Context, request Request) (*Document, error) {
			d, err := fetch(ctx, request)
			total, shape := convergenceShape(d, o)
			valid = valid && shape && total == expected
			return d, err
		}
		if _, err := discoverInitial(ctx, passOptions, first, validatingFetch, join); err != nil {
			return Inventory{}, err
		}
		passSeen := map[string]string{}
		growth := 0
		for _, source := range rows {
			id, stable, ok := convergenceIdentity(source, o)
			if !ok {
				valid = false
				continue
			}
			if previous, duplicate := passSeen[id]; duplicate && (o.Convergence.RejectDuplicates || previous != stable) {
				valid = false
			}
			if previous, exists := projections[id]; exists && previous != stable {
				valid = false
			}
			if _, exists := passSeen[id]; !exists {
				passSeen[id] = stable
			}
			if !seen[id] {
				seen[id], projections[id] = true, stable
				accumulated = append(accumulated, source)
				growth++
			}
		}
		if !valid || len(rows) != expected || len(accumulated) > expected {
			break
		}
		if pass > 1 {
			if growth == 0 {
				noGrowth++
			} else {
				noGrowth = 0
			}
			if len(accumulated) == expected && noGrowth >= o.Convergence.RequiredNoGrowth {
				proven = true
				break
			}
		}
		if pass < o.Convergence.MaxPasses {
			var err error
			first, err = fetch(ctx, Request{Method: o.Method, URL: o.Endpoint, Body: o.Body, Headers: o.Headers.Clone()})
			if err != nil {
				return Inventory{}, err
			}
		}
	}
	return projectConvergedRows(o, accumulated, join, proven)
}

func projectConvergedRows(o Options, sources []inventorySourceRow, join JoinURL, proven bool) (Inventory, error) {
	rows, indices := []map[string]any{}, []int{}
	for i, source := range sources {
		rows, indices = append(rows, source.row), append(indices, i)
	}
	var err error
	if o.ItemFilter != nil {
		indices, err = FilterItemIndices(rows, o.ItemFilter)
		if err != nil {
			return Inventory{}, err
		}
	}
	samples := []inventorySourceRow{}
	for _, i := range indices[:min(len(indices), 5)] {
		samples = append(samples, sources[i])
	}
	if o.AutoURLField && len(samples) > 0 {
		values := []map[string]any{}
		for _, source := range samples {
			values = append(values, source.row)
		}
		o.URLField = samples[0].document.FindURLField(values)
	}
	if o.AutoFields {
		o.Fields, err = autoMapFields(samples)
		if err != nil {
			return Inventory{}, err
		}
	}
	result := Inventory{Jobs: []Job{}, Truncated: !proven || len(sources) > o.MaxItems, URLOnly: len(o.Fields) == 0}
	for _, i := range indices {
		job, found, err := project(sources[i].document, sources[i].row, o, join)
		if err != nil {
			return Inventory{}, err
		}
		if found && strings.TrimSpace(job.URL) != "" {
			result.Jobs = append(result.Jobs, job)
		}
	}
	return result, nil
}
