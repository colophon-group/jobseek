package apisniffer

import (
	"encoding/json"
	"math/big"
	"strings"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	"github.com/dlclark/regexp2/v2"
	"github.com/jmespath/go-jmespath"
)

type ItemFilter struct {
	Include, Exclude map[string][]string
	ExcludeRegex     map[string][]*regexp2.Regexp
	RequireRegex     map[string]*regexp2.Regexp
	DedupeBy         []string
	Preference       *ItemPreference
}
type ItemPreference struct {
	Path             string
	Values, Fallback []string
}

func itemPath(value any) (string, error) {
	s, ok := value.(string)
	if !ok || strings.TrimSpace(s) == "" || len(s) > 4096 {
		return "", ErrOptions
	}
	s = strings.TrimSpace(s)
	if _, err := jmespath.Compile(s); err != nil {
		return "", ErrOptions
	}
	return s, nil
}
func itemStringList(value any, limit int, unique bool) ([]string, error) {
	a, ok := value.([]any)
	if !ok || len(a) == 0 || len(a) > limit {
		return nil, ErrOptions
	}
	result := []string{}
	seen := map[string]bool{}
	for _, v := range a {
		s, ok := v.(string)
		if !ok || s == "" || unique && seen[s] {
			return nil, ErrOptions
		}
		seen[s] = true
		result = append(result, s)
	}
	return result, nil
}
func itemRegex(value string, full bool) (*regexp2.Regexp, error) {
	if value == "" || len([]rune(value)) > 4096 {
		return nil, ErrOptions
	}
	if full {
		value = `\A(?:` + value + `)\Z`
	}
	return dom.CompileURLPattern(value)
}

func ItemFilterOptions(value any) (*ItemFilter, error) {
	if value == nil {
		return nil, nil
	}
	m, ok := value.(map[string]any)
	if !ok || len(m) == 0 {
		return nil, ErrOptions
	}
	f := &ItemFilter{Include: map[string][]string{}, Exclude: map[string][]string{}, ExcludeRegex: map[string][]*regexp2.Regexp{}, RequireRegex: map[string]*regexp2.Regexp{}}
	for key, v := range m {
		switch key {
		case "include", "exclude", "exclude_regex", "require_regex":
			if !detailTruthy(v) && key != "include" {
				continue
			}
			rows, ok := v.(map[string]any)
			if !ok || len(rows) > 16 || key == "include" && len(rows) == 0 {
				return nil, ErrOptions
			}
			for raw, spec := range rows {
				path, err := itemPath(raw)
				if err != nil {
					return nil, err
				}
				if key == "require_regex" {
					text, ok := spec.(string)
					if !ok {
						return nil, ErrOptions
					}
					re, err := itemRegex(text, true)
					if err != nil {
						return nil, err
					}
					f.RequireRegex[path] = re
					continue
				}
				values, err := itemStringList(spec, 100, false)
				if err != nil {
					return nil, err
				}
				if key == "include" {
					f.Include[path] = values
				} else if key == "exclude" {
					f.Exclude[path] = values
				} else {
					for _, text := range values {
						re, err := itemRegex(text, false)
						if err != nil {
							return nil, err
						}
						f.ExcludeRegex[path] = append(f.ExcludeRegex[path], re)
					}
				}
			}
		case "dedupe_by":
			if v == nil {
				continue
			}
			paths, err := itemStringList(v, 16, false)
			if err != nil {
				return nil, err
			}
			for _, raw := range paths {
				path, err := itemPath(raw)
				if err != nil {
					return nil, err
				}
				f.DedupeBy = append(f.DedupeBy, path)
			}
		case "dedupe_preference": // Validate after dedupe_by, independent of map order.
		default:
			return nil, ErrOptions
		}
	}
	if v := m["dedupe_preference"]; v != nil {
		p, ok := v.(map[string]any)
		if !ok || len(p) != 3 || len(f.DedupeBy) == 0 {
			return nil, ErrOptions
		}
		path, err := itemPath(p["path"])
		if err != nil {
			return nil, err
		}
		values, err := itemStringList(p["preferred_values"], 100, true)
		if err != nil {
			return nil, err
		}
		fallback, err := itemStringList(p["fallback_by"], 16, false)
		if err != nil {
			return nil, err
		}
		for i, raw := range fallback {
			fallback[i], err = itemPath(raw)
			if err != nil {
				return nil, err
			}
		}
		if fallback[0] != path {
			return nil, ErrOptions
		}
		f.Preference = &ItemPreference{Path: path, Values: values, Fallback: fallback}
	}
	if len(f.Include)+len(f.Exclude)+len(f.ExcludeRegex)+len(f.RequireRegex)+len(f.DedupeBy) == 0 {
		return nil, ErrOptions
	}
	return f, nil
}

func filterValues(value any) []any {
	if a, ok := value.([]any); ok {
		return a
	}
	return []any{value}
}
func filterIdentity(value any) (string, bool) {
	if s, ok := value.(string); ok {
		return s, s != ""
	}
	if n, ok := value.(json.Number); ok && !strings.ContainsAny(string(n), ".eE") {
		v, ok := new(big.Int).SetString(string(n), 10)
		if ok {
			return v.String(), true
		}
	}
	return "", false
}
func filterAnyString(value any, accepted []string) bool {
	for _, v := range filterValues(value) {
		s, ok := v.(string)
		if !ok {
			continue
		}
		for _, a := range accepted {
			if s == a {
				return true
			}
		}
	}
	return false
}

// FilterItemIndices scopes the complete raw inventory before field projection.
// Its returned positions preserve original row order and per-page provenance.
func FilterItemIndices(rows []map[string]any, f *ItemFilter) ([]int, error) {
	selected := []int{}
	for i, row := range rows {
		keep := true
		if f != nil {
			for path, accepted := range f.Include {
				v, err := Search(row, path)
				if err != nil {
					return nil, err
				}
				if !filterAnyString(v, accepted) {
					keep = false
					break
				}
			}
			if !keep {
				continue
			}
			for path, rejected := range f.Exclude {
				v, err := Search(row, path)
				if err != nil {
					return nil, err
				}
				if filterAnyString(v, rejected) {
					keep = false
					break
				}
			}
			if !keep {
				continue
			}
			for path, patterns := range f.ExcludeRegex {
				v, err := Search(row, path)
				if err != nil {
					return nil, err
				}
				for _, candidate := range filterValues(v) {
					s, ok := candidate.(string)
					if !ok {
						continue
					}
					for _, re := range patterns {
						matched, err := re.MatchString(s)
						if err != nil {
							return nil, err
						}
						if matched {
							keep = false
							break
						}
					}
					if !keep {
						break
					}
				}
				if !keep {
					break
				}
			}
			if !keep {
				continue
			}
			for path, re := range f.RequireRegex {
				v, err := Search(row, path)
				if err != nil {
					return nil, err
				}
				text, ok := filterIdentity(v)
				if !ok {
					return nil, ErrInventory
				}
				matched, err := re.MatchString(text)
				if err != nil || !matched {
					return nil, ErrInventory
				}
			}
		}
		selected = append(selected, i)
	}
	if f == nil || len(f.DedupeBy) == 0 {
		return selected, nil
	}
	keys := map[int]string{}
	winners := map[string]int{}
	preferences := map[int][]string{}
	ranks := map[int]int{}
	for _, i := range selected {
		identity := []string{}
		valid := true
		for _, path := range f.DedupeBy {
			v, err := Search(rows[i], path)
			if err != nil {
				return nil, err
			}
			text, ok := filterIdentity(v)
			if !ok {
				valid = false
				break
			}
			identity = append(identity, text)
		}
		if !valid {
			continue
		}
		encoded, _ := json.Marshal(identity)
		key := string(encoded)
		keys[i] = key
		if f.Preference != nil {
			for _, path := range f.Preference.Fallback {
				v, err := Search(rows[i], path)
				if err != nil {
					return nil, err
				}
				s, ok := v.(string)
				if !ok || s == "" {
					return nil, ErrInventory
				}
				preferences[i] = append(preferences[i], s)
			}
			rank := len(f.Preference.Values)
			for n, v := range f.Preference.Values {
				if v == preferences[i][0] {
					rank = n
					break
				}
			}
			ranks[i] = rank
		}
		previous, found := winners[key]
		better := !found
		if found && f.Preference != nil {
			if ranks[i] != ranks[previous] {
				better = ranks[i] < ranks[previous]
			} else {
				for n, text := range preferences[i] {
					if text != preferences[previous][n] {
						better = text < preferences[previous][n]
						break
					}
				}
			}
		}
		if better {
			winners[key] = i
		}
	}
	result := []int{}
	for _, i := range selected {
		key, valid := keys[i]
		if !valid || winners[key] == i {
			result = append(result, i)
		}
	}
	return result, nil
}
