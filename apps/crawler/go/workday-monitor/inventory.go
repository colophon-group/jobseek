package workday

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
)

const MaxJobs = 50000

var facetParameter = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,128}$`)
var locale = regexp.MustCompile(`^[a-z]{2}-[A-Z]{2}$`)
var copySuffix = regexp.MustCompile(`(_[^/]*[0-9])-[0-9]+$`)
var requisition = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9.-]*[0-9]$`)

// InventoryConfig preserves the monitor's URL-only discovery contract. Detail
// scraping remains separately scheduled by the caller.
type InventoryConfig struct {
	Site          Site
	AllSites      bool
	Sites         []string
	SearchText    string
	SplitFacet    string
	FacetUnion    []string
	CoverageFacet string
}

// ParseInventoryConfig applies the Python monitor's effective metadata options.
// Missing components are inferred together from the configured board URL.
func ParseInventoryConfig(boardURL string, metadata map[string]any) (InventoryConfig, error) {
	c := InventoryConfig{AllSites: true}
	c.Site.Company, _ = metadata["company"].(string)
	c.Site.Instance, _ = metadata["wd_instance"].(string)
	c.Site.Name, _ = metadata["site"].(string)
	if c.Site.Company == "" || c.Site.Instance == "" || c.Site.Name == "" {
		u, err := url.Parse(boardURL)
		if err != nil || u.Scheme != "https" || u.User != nil || u.Port() != "" {
			return c, errors.New("invalid Workday board URL")
		}
		host := strings.Split(u.Hostname(), ".")
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		if len(parts) > 1 && locale.MatchString(parts[0]) {
			parts = parts[1:]
		}
		if len(host) != 4 || host[2] != "myworkdayjobs" || host[3] != "com" || len(parts) != 1 {
			return c, errors.New("cannot infer Workday site")
		}
		c.Site = Site{host[0], host[1], parts[0]}
	}
	if value, ok := metadata["all_sites"]; ok {
		b, ok := value.(bool)
		if !ok {
			return c, errors.New("invalid all_sites")
		}
		c.AllSites = b
	}
	if value, ok := metadata["sites"]; ok {
		values, ok := value.([]any)
		if !ok || len(values) == 0 || len(values) > 20 {
			return c, errors.New("invalid explicit Workday sites")
		}
		for _, v := range values {
			s, ok := v.(string)
			if !ok {
				return c, errors.New("invalid explicit Workday site")
			}
			c.Sites = append(c.Sites, s)
		}
	}
	for key, dest := range map[string]*string{"search_text": &c.SearchText, "split_facet": &c.SplitFacet} {
		if value, ok := metadata[key]; ok && value != nil {
			s, ok := value.(string)
			if !ok || strings.TrimSpace(s) == "" {
				return c, fmt.Errorf("invalid %s", key)
			}
			*dest = s
		}
	}
	if value, ok := metadata["facet_union"]; ok && value != nil {
		m, ok := value.(map[string]any)
		if !ok || len(m) != 2 {
			return c, errors.New("invalid facet_union")
		}
		values, ok := m["facets"].([]any)
		if !ok {
			return c, errors.New("invalid facet_union facets")
		}
		for _, v := range values {
			s, ok := v.(string)
			if !ok {
				return c, errors.New("invalid facet_union facet")
			}
			c.FacetUnion = append(c.FacetUnion, s)
		}
		c.CoverageFacet, ok = m["coverage_facet"].(string)
		if !ok {
			return c, errors.New("invalid coverage_facet")
		}
	}
	return c, c.validate()
}

func (c InventoryConfig) validate() error {
	if !companyToken.MatchString(c.Site.Company) || !instanceToken.MatchString(c.Site.Instance) || !siteToken.MatchString(c.Site.Name) {
		return errors.New("invalid Workday site")
	}
	if len(c.Sites) > 0 {
		if !c.AllSites || len(c.Sites) > 20 || c.Sites[0] != c.Site.Name {
			return errors.New("explicit sites must start at the configured site and require all_sites")
		}
		seen := map[string]bool{}
		for _, s := range c.Sites {
			if !siteToken.MatchString(s) || seen[s] {
				return errors.New("invalid or duplicate explicit site")
			}
			seen[s] = true
		}
	}
	if c.SearchText != "" && (strings.TrimSpace(c.SearchText) == "" || c.AllSites || len(c.Sites) > 0) {
		return errors.New("search_text requires all_sites=false")
	}
	if c.SplitFacet != "" && !facetParameter.MatchString(c.SplitFacet) {
		return errors.New("invalid split_facet")
	}
	if len(c.FacetUnion) > 0 || c.CoverageFacet != "" {
		if c.SplitFacet != "" || len(c.FacetUnion) < 2 || len(c.FacetUnion) > 4 || !facetParameter.MatchString(c.CoverageFacet) {
			return errors.New("invalid facet_union")
		}
		seen := map[string]bool{c.CoverageFacet: true}
		for _, f := range c.FacetUnion {
			if !facetParameter.MatchString(f) || seen[f] {
				return errors.New("invalid or duplicate union dimension")
			}
			seen[f] = true
		}
	}
	return nil
}

// InventoryResult distinguishes a bounded partial inventory from a complete
// cycle. Callers must suppress gone detection whenever Truncated is true.
type InventoryResult struct {
	URLs      []string
	Requests  int
	Sites     int
	Truncated bool
}

// RobotsGetter executes the policy-approved GET of the tenant's robots.txt.
// A caller supplies an empty document for a normal unavailable robots response;
// policy refusals and cancellation must be returned as errors.
type RobotsGetter func(context.Context, string) (string, error)

// DiscoverInventory covers single and multiple sites, verified deep offsets,
// safe facet partitions, and coverage-proven facet unions. Responses stay on
// the caller's publisher-policy transport. No detail requests are made here.
func DiscoverInventory(ctx context.Context, c InventoryConfig, get RobotsGetter, post Poster) (InventoryResult, error) {
	if err := c.validate(); err != nil {
		return InventoryResult{}, err
	}
	if post == nil {
		return InventoryResult{}, errors.New("poster required")
	}
	sites := append([]string(nil), c.Sites...)
	if len(sites) == 0 && c.AllSites {
		if get == nil {
			return InventoryResult{}, errors.New("robots transport required for automatic multi-site discovery")
		}
		robots, err := get(ctx, fmt.Sprintf("https://%s.%s.myworkdayjobs.com/robots.txt", c.Site.Company, c.Site.Instance))
		if err != nil {
			return InventoryResult{}, err
		}
		seen := map[string]bool{}
		for _, line := range strings.Split(robots, "\n") {
			if !strings.HasPrefix(line, "Sitemap:") {
				continue
			}
			u, err := url.Parse(strings.TrimSpace(strings.TrimPrefix(line, "Sitemap:")))
			if err != nil {
				continue
			}
			parts := strings.Split(strings.Trim(u.Path, "/"), "/")
			if u.Hostname() == c.Site.Company+"."+c.Site.Instance+".myworkdayjobs.com" && len(parts) == 2 && parts[1] == "siteMap" && siteToken.MatchString(parts[0]) && !seen[parts[0]] {
				sites = append(sites, parts[0])
				seen[parts[0]] = true
			}
		}
	}
	if len(sites) == 0 {
		sites = []string{c.Site.Name}
	}
	d := inventoryDiscovery{ctx: ctx, post: post, slots: make(chan struct{}, 5), requests: new(atomic.Int64)}
	result := InventoryResult{Sites: len(sites)}
	seen := map[string]bool{}
	// Site order makes the configured/robots first URL win for mirrored IDs.
	// Facet queries share the same five-slot bound throughout the inventory.
	for _, name := range sites {
		site := c.Site
		site.Name = name
		paths, truncated, err := d.site(site, c)
		if err != nil {
			return InventoryResult{}, err
		}
		result.Truncated = result.Truncated || truncated
		for _, path := range paths {
			key := crossSitePathKey(path)
			if seen[key] {
				continue
			}
			seen[key] = true
			if len(result.URLs) == MaxJobs {
				result.Truncated = true
				break
			}
			result.URLs = append(result.URLs, fmt.Sprintf("https://%s.%s.myworkdayjobs.com/%s%s", site.Company, site.Instance, site.Name, path))
		}
		if len(result.URLs) == MaxJobs {
			result.Truncated = result.Truncated || c.AllSites
			break
		}
	}
	result.Requests = int(d.requests.Load())
	return result, nil
}

func crossSitePathKey(path string) string {
	path = copySuffix.ReplaceAllString(path, "${1}")
	last := path[strings.LastIndex(path, "/")+1:]
	if i := strings.LastIndex(last, "_"); i >= 0 && requisition.MatchString(last[i+1:]) {
		return "requisition:" + last[i+1:]
	}
	return path
}

type inventoryPage struct {
	Total       *int `json:"total"`
	JobPostings []struct {
		ExternalPath string `json:"externalPath"`
	} `json:"jobPostings"`
	Facets []inventoryFacet `json:"facets"`
}
type inventoryFacet struct {
	Parameter string                `json:"facetParameter"`
	Values    []inventoryFacetValue `json:"values"`
}
type inventoryFacetValue struct {
	ID        string                `json:"id"`
	Count     *int                  `json:"count"`
	Parameter string                `json:"facetParameter"`
	Values    []inventoryFacetValue `json:"values"`
}
type inventoryDiscovery struct {
	ctx      context.Context
	post     Poster
	slots    chan struct{}
	requests *atomic.Int64
}

func (d *inventoryDiscovery) fetch(rawURL string, base map[string]any, offset int) (inventoryPage, error) {
	if err := d.ctx.Err(); err != nil {
		return inventoryPage{}, err
	}
	body := map[string]any{"limit": pageSize, "offset": offset}
	for k, v := range base {
		body[k] = v
	}
	encoded, err := json.Marshal(body)
	if err != nil {
		return inventoryPage{}, err
	}
	select {
	case d.slots <- struct{}{}:
	case <-d.ctx.Done():
		return inventoryPage{}, d.ctx.Err()
	}
	defer func() { <-d.slots }()
	d.requests.Add(1)
	data, err := d.post(d.ctx, rawURL, encoded)
	if err != nil {
		return inventoryPage{}, err
	}
	var p inventoryPage
	if err = json.Unmarshal(data, &p); err != nil {
		return p, fmt.Errorf("decode Workday page: %w", err)
	}
	if offset == 0 && (p.Total == nil || *p.Total < 0) {
		return p, errors.New("invalid Workday total")
	}
	for _, job := range p.JobPostings {
		if job.ExternalPath != "" && (!strings.HasPrefix(job.ExternalPath, "/") || strings.HasPrefix(job.ExternalPath, "//") || strings.ContainsAny(job.ExternalPath, "\x00\r\n")) {
			return p, errors.New("invalid Workday externalPath")
		}
	}
	return p, nil
}
func (d *inventoryDiscovery) paginate(rawURL string, base map[string]any, abort bool) ([]string, int, []inventoryFacet, error) {
	paths := []string{}
	offset, total := 0, 0
	var facets []inventoryFacet
	for {
		p, err := d.fetch(rawURL, base, offset)
		if err != nil {
			return nil, 0, nil, err
		}
		if offset == 0 {
			total = *p.Total
			facets = p.Facets
		}
		for _, job := range p.JobPostings {
			if job.ExternalPath != "" {
				paths = append(paths, job.ExternalPath)
			}
		}
		offset += len(p.JobPostings)
		if len(p.JobPostings) == 0 || offset >= total || len(paths) >= MaxJobs || (abort && total >= resultCap) {
			break
		}
	}
	return paths, total, facets, nil
}
func uniquePaths(paths []string, canonical bool) []string {
	result := []string{}
	seen := map[string]bool{}
	for _, p := range paths {
		key := p
		if canonical {
			key = crossSitePathKey(p)
		}
		if !seen[key] {
			seen[key] = true
			result = append(result, p)
		}
	}
	return result
}
func (d *inventoryDiscovery) direct(rawURL string, base map[string]any, total int, known []string) ([]string, error) {
	paths := uniquePaths(known, false)
	seen := map[string]bool{}
	for _, p := range paths {
		seen[p] = true
	}
	expected := min(total, MaxJobs)
	for offset := 0; offset < expected; {
		p, err := d.fetch(rawURL, base, offset)
		if err != nil {
			return nil, err
		}
		if len(p.JobPostings) == 0 {
			break
		}
		offset += len(p.JobPostings)
		for _, job := range p.JobPostings {
			if job.ExternalPath != "" && !seen[job.ExternalPath] {
				seen[job.ExternalPath] = true
				paths = append(paths, job.ExternalPath)
			}
		}
	}
	if materiallyShort(len(paths), expected) {
		return nil, fmt.Errorf("Workday direct inventory returned %d of %d", len(paths), expected)
	}
	return paths, nil
}

func flattenFacets(facets []inventoryFacet) []inventoryFacet {
	result := []inventoryFacet{}
	var visit func(inventoryFacet)
	visit = func(f inventoryFacet) {
		result = append(result, f)
		for _, v := range f.Values {
			if v.Parameter != "" {
				visit(inventoryFacet{v.Parameter, v.Values})
			}
		}
	}
	for _, f := range facets {
		visit(f)
	}
	return result
}
func requiredFacet(facets []inventoryFacet, name string, safe bool) ([]inventoryFacetValue, error) {
	for _, f := range flattenFacets(facets) {
		if f.Parameter != name {
			continue
		}
		if len(f.Values) == 0 {
			return nil, errors.New("Workday facet contains no values")
		}
		seen := map[string]bool{}
		for _, v := range f.Values {
			if v.ID == "" || len(v.ID) > 512 || strings.TrimSpace(v.ID) != v.ID || strings.ContainsRune(v.ID, 0) || seen[v.ID] || v.Count == nil || *v.Count < 0 || (safe && *v.Count >= resultCap) {
				return nil, fmt.Errorf("invalid or capped Workday facet %s", name)
			}
			seen[v.ID] = true
		}
		return f.Values, nil
	}
	return nil, fmt.Errorf("Workday facet %s was not advertised", name)
}

type facetQuery struct {
	parameter string
	ids       []string
}

func facetGroups(name string, values []inventoryFacetValue) []facetQuery {
	groups := []facetQuery{}
	ids := []string{}
	count := 0
	for _, v := range values {
		budget := resultCap - 1
		if v.Count != nil {
			budget = *v.Count
		}
		if len(ids) > 0 && (count+budget >= resultCap || len(ids) >= 100) {
			groups = append(groups, facetQuery{name, ids})
			ids = nil
			count = 0
		}
		ids = append(ids, v.ID)
		count += budget
	}
	if len(ids) > 0 {
		groups = append(groups, facetQuery{name, ids})
	}
	return groups
}
func chooseFacet(facets []inventoryFacet, preferred string) (string, []inventoryFacetValue, error) {
	if preferred != "" {
		values, err := requiredFacet(facets, preferred, true)
		return preferred, values, err
	}
	var name string
	var values []inventoryFacetValue
	for _, f := range flattenFacets(facets) {
		if !facetParameter.MatchString(f.Parameter) {
			continue
		}
		candidate, err := requiredFacet([]inventoryFacet{f}, f.Parameter, true)
		if err == nil && len(candidate) > len(values) {
			name = f.Parameter
			values = candidate
		}
	}
	return name, values, nil
}

// groups retains configured query order while using five workers. This avoids
// unbounded goroutine growth on location facets with thousands of values.
func (d *inventoryDiscovery) groups(rawURL string, base map[string]any, specs []facetQuery, exact bool) ([][]string, error) {
	ctx, cancel := context.WithCancel(d.ctx)
	defer cancel()
	queries := inventoryDiscovery{ctx: ctx, post: d.post, slots: d.slots, requests: d.requests}
	results := make([][]string, len(specs))
	jobs := make(chan int)
	var wg sync.WaitGroup
	var first error
	var mu sync.Mutex
	for n := 0; n < min(5, len(specs)); n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				if ctx.Err() != nil {
					continue
				}
				body := map[string]any{}
				for k, v := range base {
					body[k] = v
				}
				body["appliedFacets"] = map[string][]string{specs[i].parameter: specs[i].ids}
				paths, total, _, err := queries.paginate(rawURL, body, false)
				if err == nil && total >= resultCap {
					err = fmt.Errorf("Workday facet group remained capped at %d", total)
				}
				if err == nil && ((exact && len(uniquePaths(paths, true)) != total) || (!exact && materiallyShort(len(uniquePaths(paths, false)), total))) {
					err = fmt.Errorf("Workday facet group incomplete: %d of %d", len(paths), total)
				}
				if err != nil {
					mu.Lock()
					if first == nil {
						first = err
						cancel()
					}
					mu.Unlock()
				} else {
					results[i] = paths
				}
			}
		}()
	}
sendLoop:
	for i := range specs {
		select {
		case jobs <- i:
		case <-ctx.Done():
			break sendLoop
		}
	}
	close(jobs)
	wg.Wait()
	if first != nil {
		return nil, first
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return results, nil
}
func coverageTotal(facets []inventoryFacet, name string, advertised int) (int, error) {
	values, err := requiredFacet(facets, name, false)
	if err != nil {
		return 0, err
	}
	total := 0
	for _, v := range values {
		if *v.Count > int(^uint(0)>>1)-total {
			return 0, errors.New("Workday facet count overflow")
		}
		total += *v.Count
	}
	if total <= 0 || total < advertised {
		return 0, errors.New("Workday independent coverage count incomplete")
	}
	return total, nil
}
func (d *inventoryDiscovery) union(rawURL string, base map[string]any, total int, facets []inventoryFacet, c InventoryConfig) ([]string, error) {
	if _, err := coverageTotal(facets, c.CoverageFacet, total); err != nil {
		return nil, err
	}
	queries := []facetQuery{}
	for _, name := range c.FacetUnion {
		values, err := requiredFacet(facets, name, true)
		if err != nil {
			return nil, err
		}
		queries = append(queries, facetGroups(name, values)...)
	}
	results, err := d.groups(rawURL, base, queries, true)
	if err != nil {
		return nil, err
	}
	byKey := map[string]string{}
	for _, paths := range results {
		for _, path := range paths {
			key := crossSitePathKey(path)
			if _, ok := byKey[key]; !ok {
				byKey[key] = path
			}
		}
	}
	_, finalTotal, finalFacets, err := d.paginate(rawURL, base, true)
	if err != nil {
		return nil, err
	}
	coverage, err := coverageTotal(finalFacets, c.CoverageFacet, finalTotal)
	if err != nil {
		return nil, err
	}
	if len(byKey) != coverage {
		return nil, fmt.Errorf("Workday facet union returned %d of %d coverage-proven jobs", len(byKey), coverage)
	}
	keys := make([]string, 0, len(byKey))
	for key := range byKey {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	paths := make([]string, len(keys))
	for i, key := range keys {
		paths[i] = byKey[key]
	}
	return paths, nil
}
func (d *inventoryDiscovery) site(site Site, c InventoryConfig) ([]string, bool, error) {
	rawURL := fmt.Sprintf("https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs", site.Company, site.Instance, site.Company, site.Name)
	base := map[string]any{}
	if c.SearchText != "" {
		base["searchText"] = c.SearchText
	}
	paths, total, facets, err := d.paginate(rawURL, base, true)
	if err != nil {
		return nil, false, err
	}
	paths = uniquePaths(paths, false)
	expected := min(total, MaxJobs)
	if total < resultCap {
		if materiallyShort(len(paths), expected) {
			paths, err = d.direct(rawURL, base, total, paths)
		}
	} else if len(c.FacetUnion) > 0 {
		paths, err = d.union(rawURL, base, total, facets, c)
	} else {
		name, values, pickErr := chooseFacet(facets, c.SplitFacet)
		if pickErr != nil {
			return nil, false, pickErr
		}
		if name == "" {
			paths, err = d.direct(rawURL, base, total, nil)
		} else {
			groups, groupErr := d.groups(rawURL, base, facetGroups(name, values), false)
			if groupErr != nil {
				return nil, false, groupErr
			}
			paths = nil
			for _, group := range groups {
				paths = append(paths, group...)
			}
			paths = uniquePaths(paths, false)
			if materiallyShort(len(paths), expected) {
				paths, err = d.direct(rawURL, base, total, paths)
			}
		}
	}
	if err != nil {
		return nil, false, err
	}
	if materiallyShort(len(paths), expected) {
		return nil, false, fmt.Errorf("Workday inventory incomplete: %d of %d", len(paths), expected)
	}
	truncated := total > MaxJobs || len(paths) > MaxJobs
	if len(paths) > MaxJobs {
		paths = paths[:MaxJobs]
	}
	return paths, truncated, nil
}
