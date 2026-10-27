package dom

import (
	"errors"
	"net/url"
	"sort"
	"strconv"
	"strings"

	"github.com/dlclark/regexp2/v2"
)

var ErrListingPartition = errors.New("DOM partition inventory could not be completed")
var ErrPartitionSnapshot = errors.New("DOM counted partition snapshot changed")

// Partition paths may overlap. Each bounded leaf must match its count and the
// union of redundant paths must cover its parent before any jobs are returned.
type ListingPartitions struct {
	Selector                 string
	Fallback                 []string
	CoverPaths               [][]string
	DropParams               []string
	CountPattern             *regexp2.Regexp
	Limit                    int
	ValidateTotal, Stateless bool
}

var partitionKeys = []string{"partition_selector", "partition_fallback_selector", "partition_fallback_selectors", "partition_cover_paths", "partition_drop_params", "partition_count_regex", "partition_result_limit", "partition_validate_total", "partition_stateless"}

func listingPartitions(m Object) (*ListingPartitions, error) {
	if m["partition_selector"] == nil {
		for _, k := range partitionKeys[1:] {
			if m[k] != nil {
				return nil, ErrListingPartition
			}
		}
		return nil, nil
	}
	p := &ListingPartitions{}
	var err error
	if p.Selector, err = proofSelector(m["partition_selector"], true); err != nil {
		return nil, ErrListingPartition
	}
	for k, target := range map[string]*bool{"partition_validate_total": &p.ValidateTotal, "partition_stateless": &p.Stateless} {
		if v := m[k]; v != nil {
			var ok bool
			*target, ok = v.(bool)
			if !ok {
				return nil, ErrListingPartition
			}
		}
	}
	selectors := func(v any, minimum, maximum int) ([]string, error) {
		values, ok := v.([]any)
		if !ok || len(values) < minimum || len(values) > maximum {
			return nil, ErrListingPartition
		}
		out := []string{}
		seen := map[string]bool{}
		for _, raw := range values {
			s, e := proofSelector(raw, true)
			if e != nil || seen[s] {
				return nil, ErrListingPartition
			}
			seen[s] = true
			out = append(out, s)
		}
		return out, nil
	}
	modes := 0
	if v := m["partition_fallback_selector"]; v != nil {
		modes++
		s, e := proofSelector(v, true)
		if e != nil {
			return nil, ErrListingPartition
		}
		p.Fallback = []string{s}
	}
	if v := m["partition_fallback_selectors"]; v != nil {
		modes++
		p.Fallback, err = selectors(v, 1, 4)
		if err != nil {
			return nil, err
		}
	}
	if v := m["partition_cover_paths"]; v != nil {
		modes++
		paths, ok := v.([]any)
		if !ok || len(paths) < 2 || len(paths) > 4 {
			return nil, ErrListingPartition
		}
		seen := map[string]bool{}
		for _, raw := range paths {
			path, e := selectors(raw, 1, 4)
			key := strings.Join(path, "\x00")
			if e != nil || seen[key] {
				return nil, ErrListingPartition
			}
			seen[key] = true
			p.CoverPaths = append(p.CoverPaths, path)
		}
	}
	if modes > 1 {
		return nil, ErrListingPartition
	}
	if v := m["partition_drop_params"]; v != nil {
		values, ok := v.([]any)
		if !ok || len(values) > 16 {
			return nil, ErrListingPartition
		}
		for _, raw := range values {
			s, ok := raw.(string)
			if !ok || s == "" || len(s) > 256 || strings.ContainsRune(s, 0) {
				return nil, ErrListingPartition
			}
			p.DropParams = append(p.DropParams, s)
		}
	}
	if v := m["partition_count_regex"]; v != nil {
		s, ok := v.(string)
		if !ok {
			return nil, ErrListingPartition
		}
		if s != "" {
			p.CountPattern, err = CompileURLPattern("(?i)" + s)
			if err != nil {
				return nil, ErrListingPartition
			}
		}
	}
	if v := m["partition_result_limit"]; v != nil {
		if _, bad := v.(bool); bad {
			return nil, ErrListingPartition
		}
		p.Limit, err = number(v, 0)
		if err != nil || p.Limit < 1 {
			return nil, ErrListingPartition
		}
	}
	if modes > 0 && (p.CountPattern == nil || p.Limit == 0) || p.ValidateTotal && p.CountPattern == nil || len(p.CoverPaths) > 0 && !p.ValidateTotal {
		return nil, ErrListingPartition
	}
	return p, nil
}

func (p *ListingPartitions) Count(source string) (int, error) {
	if p.CountPattern == nil {
		return -1, nil
	}
	texts, err := titleScanTexts(source, "title")
	if err != nil || len(texts) == 0 {
		return 0, ErrListingPartition
	}
	m, err := p.CountPattern.FindStringMatch(texts[0])
	if err != nil || m == nil || len(m.Groups()) < 2 {
		return 0, ErrListingPartition
	}
	n, err := strconv.Atoi(m.GroupByNumber(1).String())
	if err != nil || n < 0 {
		return 0, ErrListingPartition
	}
	return n, nil
}

type partitionQueryPair struct{ name, value string }

func partitionPairs(raw string) ([]partitionQueryPair, error) {
	out := []partitionQueryPair{}
	for _, pair := range strings.Split(raw, "&") {
		if pair == "" {
			continue
		}
		k, v, _ := strings.Cut(pair, "=")
		k, e := url.QueryUnescape(k)
		if e != nil {
			return nil, e
		}
		v, e = url.QueryUnescape(v)
		if e != nil {
			return nil, e
		}
		out = append(out, partitionQueryPair{k, v})
	}
	return out, nil
}

// Match urllib's inherited ordered query pairs, including blank values. A
// child replaces all values of a name; state-changing facet flags are dropped.
func (p *ListingPartitions) CanonicalURL(raw, inherited string) (string, error) {
	u, e := url.Parse(raw)
	if e != nil {
		return "", ErrListingPartition
	}
	parent, e := url.Parse(inherited)
	if e != nil {
		return "", ErrListingPartition
	}
	if !p.ResourceMatches(inherited, raw) {
		return "", ErrListingPartition
	}
	current, e := partitionPairs(u.RawQuery)
	if e != nil {
		return "", ErrListingPartition
	}
	previous, e := partitionPairs(parent.RawQuery)
	if e != nil {
		return "", ErrListingPartition
	}
	names := map[string]bool{}
	drop := map[string]bool{}
	for _, pair := range current {
		names[pair.name] = true
	}
	for _, name := range p.DropParams {
		drop[name] = true
	}
	pairs := []partitionQueryPair{}
	for _, pair := range previous {
		if !names[pair.name] {
			pairs = append(pairs, pair)
		}
	}
	pairs = append(pairs, current...)
	encoded := []string{}
	for _, pair := range pairs {
		if !drop[pair.name] {
			encoded = append(encoded, url.QueryEscape(pair.name)+"="+url.QueryEscape(pair.value))
		}
	}
	u.RawQuery = strings.Join(encoded, "&")
	u.ForceQuery = false
	return u.String(), nil
}

func (p *ListingPartitions) ResourceMatches(endpoint, raw string) bool {
	a, e := url.Parse(endpoint)
	if e != nil {
		return false
	}
	b, e := url.Parse(raw)
	return e == nil && len(raw) <= 8192 && a.Scheme == "https" && b.Scheme == a.Scheme && b.Host == a.Host && b.Path == a.Path && b.RawPath == a.RawPath && b.User == nil && b.Opaque == "" && b.Fragment == ""
}

func (p *ListingPartitions) Links(source, endpoint, selector string) ([]string, error) {
	hrefs, e := ListingHrefs(source, selector)
	if e != nil {
		return nil, e
	}
	base, e := url.Parse(endpoint)
	if e != nil {
		return nil, ErrListingPartition
	}
	seen := map[string]bool{}
	for _, href := range hrefs {
		ref, e := url.Parse(href)
		if e != nil {
			return nil, ErrListingPartition
		}
		raw, e := p.CanonicalURL(base.ResolveReference(ref).String(), endpoint)
		if e != nil {
			return nil, e
		}
		seen[raw] = true
	}
	if len(seen) == 0 || len(seen) > 500 {
		return nil, ErrListingPartition
	}
	out := []string{}
	for raw := range seen {
		out = append(out, raw)
	}
	sort.Strings(out)
	return out, nil
}
