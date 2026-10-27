package dom

import (
	"encoding/json"
	"strings"
	"testing"
)

func partitionOptionsForTest(t *testing.T, body string) (*ListingPagination, error) {
	t.Helper()
	var v any
	d := json.NewDecoder(strings.NewReader(body))
	d.UseNumber()
	if e := d.Decode(&v); e != nil {
		t.Fatal(e)
	}
	return listingPagination(v, "https://jobs.example.com/list?lang=en")
}

func TestListingPartitionsOrderedInheritedQueries(t *testing.T) {
	p, e := partitionOptionsForTest(t, `{"param_name":"page","partition_selector":"a.facet","partition_drop_params":["changefacet"]}`)
	if e != nil {
		t.Fatal(e)
	}
	got, e := p.Partitions.CanonicalURL("https://jobs.example.com/list?changefacet=1&facet=%C3%A9&tag=x&tag=y", "https://jobs.example.com/list?lang=en&blank=&tag=old")
	if e != nil || got != "https://jobs.example.com/list?lang=en&blank=&facet=%C3%A9&tag=x&tag=y" {
		t.Fatal("inherited query ordering or blank values changed")
	}
	for _, raw := range []string{"https://other.example.com/list?facet=a", "http://jobs.example.com/list?facet=a", "https://jobs.example.com/other?facet=a", "https://user@jobs.example.com/list?facet=a", "https://jobs.example.com/list?facet=a#other"} {
		if p.Partitions.ResourceMatches("https://jobs.example.com/list", raw) {
			t.Fatal("unbound partition resource admitted")
		}
	}
}

func TestListingPartitionsInvalidContracts(t *testing.T) {
	for _, body := range []string{
		`{"param_name":"page","partition_count_regex":"(x)"}`,
		`{"param_name":"page","partition_selector":"a","partition_stateless":1}`,
		`{"param_name":"page","partition_selector":"a","partition_result_limit":true}`,
		`{"param_name":"page","partition_selector":"a","partition_result_limit":1.5}`,
		`{"param_name":"page","partition_selector":"a","partition_cover_paths":[["a"],["b"]]}`,
		`{"param_name":"page","partition_selector":"a","partition_fallback_selector":"a","partition_fallback_selectors":["b"]}`,
		`{"param_name":"page","partition_selector":"a","partition_count_regex":"("}`,
	} {
		if _, e := partitionOptionsForTest(t, body); e == nil {
			t.Fatal("invalid partition contract admitted")
		}
	}
}
