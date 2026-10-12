package dom

import (
	"encoding/json"
	"testing"
)

func TestRichStaticPaginationBindsExplicitEmptyProof(t *testing.T) {
	config := Object{"rich_rows": json.RawMessage(`{"row_selector":"article","link_selector":"a"}`), "pagination": map[string]any{"param_name": "offset", "start": 0, "increment": 20}, "empty_states": []any{map[string]any{"selector": ".empty", "exact_text": "No jobs", "forbidden_link_selector": "article a"}}}
	c, e := ListingOptions(config, "https://example.com/careers")
	if e != nil || c.RichRows == nil || c.Pagination == nil || len(c.Proofs.EmptyStates) != 1 || c.Document.MaxBodyBytes != 2<<20 {
		t.Fatal("static rich pagination proof lost", e)
	}
	config["render"] = true
	if _, e = ListingOptions(config, "https://example.com/careers"); e == nil {
		t.Fatal("rendered combination admitted without physical authority")
	}
}
