package dom

import "testing"

func TestListingAdvertisedRangesRequireCompleteContiguousRoot(t *testing.T) {
	c, err := ListingOptions(Object{"link_selector": "a.job", "pagination": map[string]any{"param_name": "p_start", "increment": 10, "advertised_ranges": map[string]any{"selector": "li", "pattern": `^(\d+)-(\d+)$`}}, "empty_states": []any{map[string]any{"selector": ".empty", "exact_text": "No jobs", "forbidden_link_selector": "a.job"}}}, "https://example.com/careers")
	if err != nil {
		t.Fatal(err)
	}
	for _, source := range []string{`<li>21-23</li><li>1-10</li><li>11-20</li><li>1-10</li>`, `<li><b>1-10</b></li><li>11-20</li><li>21-23</li>`} {
		total, err := ListingRangeTotal(source, c.Pagination.AdvertisedRanges, 10)
		if err != nil || total == nil || *total != 23 {
			t.Fatal("advertised root range changed", err)
		}
	}
	for _, source := range []string{`<li>11-20</li>`, `<li>1-10</li><li>12-20</li>`, `<li>1-10</li><li>10-20</li>`, `<li>1-10</li><li>11-50001</li>`, `<li>0-10</li>`, `<li>1-9</li>`, `<li>1-10 extra</li>`, `<li>1-10</li><li>1-20</li>`} {
		if _, err := ListingRangeTotal(source, c.Pagination.AdvertisedRanges, 10); err == nil {
			t.Fatal("incomplete or conflicting advertised ranges accepted", source)
		}
	}
}

func TestListingAdvertisedRangesRejectMixedContracts(t *testing.T) {
	for _, ranges := range []any{false, map[string]any{"selector": "li", "pattern": `(\d+)`}, map[string]any{"selector": "[", "pattern": `(\d+)-(\d+)`}, map[string]any{"selector": "li", "pattern": `(\d+)-(\d+)`, "extra": true}} {
		if _, err := ListingOptions(Object{"pagination": map[string]any{"param_name": "page", "advertised_ranges": ranges}}, "https://example.com/jobs"); err == nil {
			t.Fatal("invalid advertised range configuration accepted")
		}
	}
}
