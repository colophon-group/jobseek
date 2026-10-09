package dom

import "testing"

func TestPaginatedTotalReadsRootProofAndRetainsNegativeCursor(t *testing.T) {
	c, e := ListingOptions(Object{"link_selector": "a.job", "pagination": map[string]any{"param_name": "page", "start": -1}, "advertised_total": map[string]any{"selector": ".total", "regex": `(\d+) jobs`}}, "https://example.com/careers?region=CH")
	if e != nil {
		t.Fatal(e)
	}
	if c.Pagination.URL(c.BoardURL, 2) != "https://example.com/careers?region=CH&page=0" || c.Pagination.URL(c.BoardURL, 3) != "https://example.com/careers?region=CH&page=1" {
		t.Fatal("negative initial cursor changed")
	}
	for _, body := range []string{`<div class="total">2 <span>jobs</span></div>`, `<div class="total">٢ jobs</div><div class="total">2 jobs</div>`} {
		total, e := ListingAdvertisedTotal(body, c.Proofs)
		if e != nil || total == nil || *total != 2 {
			t.Fatal("root total text/decimal semantics changed", e)
		}
	}
	for _, body := range []string{`<p>No total</p>`, `<div class="total">2 jobs</div><div class="total">3 jobs</div>`, `<div class="total">2 jobs unavailable</div>`} {
		if _, e := ListingAdvertisedTotal(body, c.Proofs); e == nil {
			t.Fatal("missing/conflicting/nonmatching total accepted")
		}
	}
}
