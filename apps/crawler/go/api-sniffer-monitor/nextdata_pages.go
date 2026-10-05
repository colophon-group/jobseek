package apisniffer

import (
	"encoding/json"
	"math"
	"strconv"
	"strings"
)

func (d *Document) NextdataPageItems(o NextdataOptions) ([]any, error) {
	v, err := Search(d.Value, o.Path)
	if err != nil {
		return nil, err
	}
	if rows, ok := v.([]any); ok {
		return rows, nil
	}
	if o.Source != "rsc" {
		return nil, ErrInventory
	}
	// RSC component indexes can change. Walk original provider key order and
	// accept the first array whose first five entries have the actual witnesses.
	var find func(any) []any
	find = func(value any) []any {
		switch v := value.(type) {
		case []any:
			looksLike := len(v) >= 5
			if looksLike {
				for _, raw := range v[:5] {
					row, ok := raw.(map[string]any)
					if !ok || row["id"] == nil {
						looksLike = false
						break
					}
					description := detailTruthy(row["description"]) || detailTruthy(row["jobDescription"]) || detailTruthy(row["content"])
					title := detailTruthy(row["title"]) || detailTruthy(row["name"]) || detailTruthy(row["jobTitle"]) || detailTruthy(row["job_title"])
					if position, ok := row["position"].(map[string]any); ok {
						title = title || detailTruthy(position["name"]) || detailTruthy(position["title"])
					}
					if !description || !title {
						looksLike = false
						break
					}
				}
			}
			if looksLike {
				return v
			}
			for _, child := range v {
				if found := find(child); found != nil {
					return found
				}
			}
		case map[string]any:
			for _, key := range d.ObjectKeys(v) {
				if found := find(v[key]); found != nil {
					return found
				}
			}
		}
		return nil
	}
	if found := find(d.Value); found != nil {
		return found, nil
	}
	return nil, ErrInventory
}

func nextdataCount(value any) (int, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, true
		}
		return 0, true
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(v))
		return n, err == nil
	case json.Number:
		if n, err := strconv.Atoi(v.String()); err == nil {
			return n, true
		}
		f, err := strconv.ParseFloat(v.String(), 64)
		if err != nil || math.IsInf(f, 0) || math.IsNaN(f) || f > math.MaxInt32 || f < math.MinInt32 {
			return 0, false
		}
		return int(f), true
	}
	return 0, false
}

func (d *Document) NextdataPageCount(o NextdataOptions) (int, *int, error) {
	if o.Pagination == nil {
		return 1, nil, nil
	}
	p := o.Pagination
	parent, err := Search(d.Value, p.Path)
	if err != nil {
		return 0, nil, err
	}
	object, ok := parent.(map[string]any)
	if !ok {
		return 0, nil, ErrInventory
	}
	var total *int
	if p.TotalRecords != "" {
		v, err := Search(object, p.TotalRecords)
		if err != nil {
			return 0, nil, err
		}
		if n, ok := nextdataCount(v); ok && n >= 0 {
			total = &n
		}
	}
	if p.PageCount != "" {
		v, err := Search(object, p.PageCount)
		if err != nil {
			return 0, nil, err
		}
		count, ok := nextdataCount(v)
		if !ok {
			return 0, nil, ErrInventory
		}
		return count, total, nil
	}
	if total == nil || p.Size <= 0 {
		return 0, nil, ErrInventory
	}
	return (*total + p.Size - 1) / p.Size, total, nil
}

func (o NextdataOptions) FilterNextdataItems(items []any) ([]any, error) {
	if len(o.IncludeItems) > 0 {
		included := []any{}
		for _, raw := range items {
			item, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			keep := true
			for path, expected := range o.IncludeItems {
				v, err := Search(item, path)
				if err != nil {
					return nil, err
				}
				matched := false
				values := []any{v}
				if list, ok := v.([]any); ok {
					values = list
				}
				for _, v := range values {
					if s, ok := v.(string); ok {
						for _, want := range expected {
							matched = matched || s == want
						}
					}
				}
				if !matched {
					keep = false
					break
				}
			}
			if keep {
				included = append(included, item)
			}
		}
		items = included
	}
	for _, raw := range items {
		if len(o.RequireItems) == 0 {
			break
		}
		item, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrInventory
		}
		for path, expected := range o.RequireItems {
			v, err := Search(item, path)
			if err != nil {
				return nil, err
			}
			values, ok := v.([]any)
			if !ok || len(values) != len(expected) {
				return nil, ErrInventory
			}
			for i, want := range expected {
				if values[i] != want {
					return nil, ErrInventory
				}
			}
		}
	}
	return items, nil
}
