package workday

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"testing"
)

func TestInventoryConfigurationPreservesEnabledWorkdayOptions(t *testing.T) {
	metadata := map[string]any{"all_sites": false, "search_text": " Brand ", "split_facet": "locations"}
	c, err := ParseInventoryConfig("https://example.wd5.myworkdayjobs.com/en-US/External", metadata)
	if err != nil || c.Site != (Site{"example", "wd5", "External"}) || c.SearchText != " Brand " || c.SplitFacet != "locations" || c.AllSites {
		t.Fatalf("config=%+v err=%v", c, err)
	}
	for _, bad := range []map[string]any{
		{"search_text": "Brand"},
		{"sites": []any{"Other", "External"}},
		{"sites": []any{"External", "External"}},
		{"sites": []any{"External"}, "all_sites": false},
		{"split_facet": "bad facet"},
		{"facet_union": map[string]any{"facets": []any{"locations", "category"}, "coverage_facet": "locations"}},
		{"facet_union": map[string]any{"facets": []any{"locations", "category"}, "coverage_facet": "coverage"}, "split_facet": "locations"},
	} {
		if _, err := ParseInventoryConfig("https://example.wd5.myworkdayjobs.com/External", bad); err == nil {
			t.Fatalf("accepted invalid config %v", bad)
		}
	}
}

func TestInventoryMultipleSitesRetainsCanonicalRequisitionWinner(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}, AllSites: true}
	get := func(_ context.Context, rawURL string) (string, error) {
		if rawURL != "https://example.wd5.myworkdayjobs.com/robots.txt" {
			t.Fatal(rawURL)
		}
		return "Sitemap: https://example.wd5.myworkdayjobs.com/External/siteMap\nSitemap: https://example.wd5.myworkdayjobs.com/Brand/siteMap\nSitemap: https://foreign.wd5.myworkdayjobs.com/Other/siteMap\n", nil
	}
	post := func(_ context.Context, rawURL string, _ []byte) ([]byte, error) {
		if strings.Contains(rawURL, "/External/") {
			return page(2, "/job/Engineer_R-123456", "/job/Other_8877"), nil
		}
		if !strings.Contains(rawURL, "/Brand/") {
			t.Fatalf("unexpected site: %s", rawURL)
		}
		return page(2, "/job/Translated-title_R-123456-2", "/job/Extra_9900"), nil
	}
	r, err := DiscoverInventory(context.Background(), c, get, post)
	if err != nil || len(r.URLs) != 3 || r.Sites != 2 || r.Requests != 2 || r.Truncated {
		t.Fatalf("result=%+v err=%v", r, err)
	}
	if r.URLs[0] != "https://example.wd5.myworkdayjobs.com/External/job/Engineer_R-123456" || !strings.Contains(r.URLs[2], "/Brand/") {
		t.Fatal(r.URLs)
	}
	assertPythonInventoryHash(t, r.URLs, "5ca117cb8743701f1b832333a400c7b3474f571429ff0a1df4f118bcd1840441")
	// A policy refusal on robots must not be hidden by a configured-site retry.
	refusal := errors.New("publisher reservation")
	_, err = DiscoverInventory(context.Background(), c, func(context.Context, string) (string, error) { return "", refusal }, post)
	if !errors.Is(err, refusal) {
		t.Fatal("publisher refusal was lost")
	}
}

type inventoryFixtureRequest struct {
	Limit  int                 `json:"limit"`
	Offset int                 `json:"offset"`
	Search string              `json:"searchText"`
	Facets map[string][]string `json:"appliedFacets"`
}

func fixturePage(total int, offset int, ids []int, facets any) []byte {
	end := min(len(ids), offset+20)
	postings := []map[string]string{}
	if offset < len(ids) {
		for _, id := range ids[offset:end] {
			postings = append(postings, map[string]string{"externalPath": fmt.Sprintf("/job/Engineer_R-%06d", id)})
		}
	}
	b, _ := json.Marshal(map[string]any{"total": total, "jobPostings": postings, "facets": facets})
	return b
}
func numbered(n int) []int {
	ids := make([]int, n)
	for i := range ids {
		ids[i] = i + 1
	}
	return ids
}

func TestInventoryDeepPaginationPreservesSearchAndRejectsCap(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}, SearchText: "Brand"}
	for _, capped := range []bool{false, true} {
		calls := 0
		post := func(_ context.Context, _ string, body []byte) ([]byte, error) {
			calls++
			var request inventoryFixtureRequest
			if err := json.Unmarshal(body, &request); err != nil {
				return nil, err
			}
			if request.Limit != 20 || request.Search != "Brand" {
				t.Fatalf("search/limit lost: %s", body)
			}
			ids := numbered(2040)
			if capped {
				ids = ids[:2000]
			}
			return fixturePage(2040, request.Offset, ids, nil), nil
		}
		r, err := DiscoverInventory(context.Background(), c, nil, post)
		if capped {
			if err == nil || len(r.URLs) > 0 {
				t.Fatal("accepted a materially capped direct inventory")
			}
			continue
		}
		if err != nil || len(r.URLs) != 2040 || calls != 103 || r.Truncated {
			t.Fatalf("result count=%d calls=%d err=%v", len(r.URLs), calls, err)
		}
		assertPythonInventoryHash(t, r.URLs, "fa16416e6161d895e68a1c9b788d33f30847cdbb986659beb528b554a71e5989")
	}
}

func TestInventoryNestedFacetPartitionAndIncompleteDirectRecovery(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}, SplitFacet: "locations"}
	ids := numbered(2040)
	for _, missing := range []bool{false, true} {
		post := func(_ context.Context, _ string, body []byte) ([]byte, error) {
			var request inventoryFixtureRequest
			if err := json.Unmarshal(body, &request); err != nil {
				return nil, err
			}
			facets := []any{map[string]any{"facetParameter": "locationMainGroup", "values": []any{map[string]any{"facetParameter": "locations", "values": []any{map[string]any{"id": "A", "count": 1020}, map[string]any{"id": "B", "count": 1020}}}}}}
			if len(request.Facets) == 0 {
				return fixturePage(2040, request.Offset, ids, facets), nil
			}
			group := request.Facets["locations"]
			if len(group) != 1 {
				t.Fatalf("capped OR group %v", group)
			}
			chosen := ids[:1020]
			if group[0] == "B" {
				chosen = ids[1020:]
			}
			if missing && group[0] == "B" {
				chosen = chosen[:980]
			}
			return fixturePage(len(chosen), request.Offset, chosen, nil), nil
		}
		r, err := DiscoverInventory(context.Background(), c, nil, post)
		if err != nil || len(r.URLs) != 2040 {
			t.Fatalf("missing=%v count=%d err=%v", missing, len(r.URLs), err)
		}
		if missing && r.Requests < 200 {
			t.Fatal("missing facet jobs were not recovered by verified direct offsets")
		}
	}
}

func TestInventoryFacetUnionProvesFreshIndependentCoverage(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}, FacetUnion: []string{"category", "locations"}, CoverageFacet: "coverage"}
	ids := numbered(2001)
	for _, omit := range []bool{false, true} {
		var mu sync.Mutex
		maxActive, active := 0, 0
		post := func(_ context.Context, _ string, body []byte) ([]byte, error) {
			mu.Lock()
			active++
			maxActive = max(maxActive, active)
			mu.Unlock()
			defer func() { mu.Lock(); active--; mu.Unlock() }()
			var request inventoryFixtureRequest
			if err := json.Unmarshal(body, &request); err != nil {
				return nil, err
			}
			facets := []any{map[string]any{"facetParameter": "coverage", "values": []any{map[string]any{"id": "all", "count": 2001}}}, map[string]any{"facetParameter": "category", "values": []any{map[string]any{"id": "category-present", "count": 1200}}}, map[string]any{"facetParameter": "locations", "values": []any{map[string]any{"id": "location-present", "count": 1200}}}}
			chosen := ids
			if _, ok := request.Facets["category"]; ok {
				chosen = ids[:1200]
			}
			if _, ok := request.Facets["locations"]; ok {
				chosen = ids[801:]
				if omit {
					chosen = chosen[:1199]
				}
			}
			total := len(chosen)
			if len(request.Facets) == 0 {
				total = 2000
			}
			return fixturePage(total, request.Offset, chosen, facets), nil
		}
		r, err := DiscoverInventory(context.Background(), c, nil, post)
		if omit {
			if err == nil || len(r.URLs) > 0 {
				t.Fatal("accepted incomplete union with a null-valued posting")
			}
			continue
		}
		if err != nil || len(r.URLs) != 2001 || r.Truncated || maxActive > 5 {
			t.Fatalf("count=%d concurrency=%d err=%v", len(r.URLs), maxActive, err)
		}
		if !strings.HasSuffix(r.URLs[0], "R-000001") {
			t.Fatal("union order is not deterministic")
		}
		assertPythonInventoryHash(t, r.URLs, "56f5002924baa7f9766463edd3966bf4531168c5bdfa8b1f69bd3885a2eaa77d")
	}
}

// These canonical hashes were observed from the actual Python workday.discover
// on identical httpx.MockTransport fixtures, including configured metadata.
func assertPythonInventoryHash(t *testing.T, urls []string, want string) {
	t.Helper()
	canonical := append([]string(nil), urls...)
	sort.Strings(canonical)
	body, err := json.Marshal(canonical)
	if err != nil {
		t.Fatal(err)
	}
	if got := fmt.Sprintf("%x", sha256.Sum256(body)); got != want {
		t.Fatalf("Python canonical inventory hash differs: %s", got)
	}
}

func TestInventoryTruncationAndCancellationCannotBecomeComplete(t *testing.T) {
	c := InventoryConfig{Site: Site{"example", "wd5", "External"}}
	ids := numbered(MaxJobs + 20)
	post := func(_ context.Context, _ string, body []byte) ([]byte, error) {
		var r inventoryFixtureRequest
		json.Unmarshal(body, &r)
		return fixturePage(len(ids), r.Offset, ids, nil), nil
	}
	r, err := DiscoverInventory(context.Background(), c, nil, post)
	if err != nil || len(r.URLs) != MaxJobs || !r.Truncated {
		t.Fatalf("count=%d truncated=%v err=%v", len(r.URLs), r.Truncated, err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := DiscoverInventory(ctx, c, nil, post); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled inventory: %v", err)
	}
}
