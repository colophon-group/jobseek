package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

// Hyatt's public faceted-search POST is covered by the existing HTTP API
// monitor. Keep a source-shaped fixture so Enterprise support does not require
// a second, less bounded paginator for this tenant.
func TestTaleoEnterpriseHyattFacetedSearchUsesOriginalHTTPSniffer(t *testing.T) {
	request, err := os.ReadFile("../../tests/fixtures/taleo_enterprise/hyatt_request.json")
	if err != nil {
		t.Fatal(err)
	}
	firstRaw, err := os.ReadFile("../../tests/fixtures/taleo_enterprise/hyatt_listing_page1.json")
	if err != nil {
		t.Fatal(err)
	}
	var first map[string]any
	if err := json.Unmarshal(firstRaw, &first); err != nil {
		t.Fatal(err)
	}
	first["pagingData"].(map[string]any)["totalCount"] = float64(26)
	second := map[string]any{
		"pagingData":      map[string]any{"currentPageNo": 2, "pageSize": 25, "totalCount": 26},
		"requisitionList": []any{map[string]any{"jobId": "unique-second", "contestNo": "HKG000001", "column": []any{"Guest Services Agent", "Hyatt Regency Hong Kong, Tsim Sha Tsui", "Guest Services", `["HK-Hong Kong-Tsim Sha Tsui"]`, "Oct 30, 2026"}}},
	}
	metadata := map[string]any{
		"api_url": "https://hyatt.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal=460210089",
		"method":  "POST", "post_data": json.RawMessage(request),
		"request_headers": map[string]string{"tz": "Europe/Zurich", "tzname": "Europe/Zurich"},
		"json_path":       "requisitionList", "total_path": "pagingData.totalCount",
		"url_template": "https://hyatt.taleo.net/careersection/1/jobdetail.ftl?job={contestNo}&lang=en",
		"fields":       map[string]any{"title": "column[0]", "date_posted": "column[4]"},
		"pagination":   map[string]any{"param_name": "pageNo", "style": "page", "location": "body", "start_value": 1, "increment": 1, "max_pages": 200},
	}
	encoded, _ := json.Marshal(metadata)
	o, err := OptionsFromMetadata("https://hyatt.taleo.net/careersection/1/jobsearch.ftl?lang=en", string(encoded))
	if err != nil {
		t.Fatal(err)
	}
	calls := 0
	out, err := Discover(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
		calls++
		if r.Method != "POST" || !strings.HasPrefix(r.URL, "https://hyatt.taleo.net/careersection/rest/jobboard/searchjobs?") || r.Headers.Get("tz") == "" {
			t.Fatal("source request changed", r.Method, r.URL)
		}
		var body map[string]any
		if err := json.Unmarshal([]byte(r.Body), &body); err != nil || int(body["pageNo"].(float64)) != calls {
			t.Fatal("source page cursor changed", r.Body, err)
		}
		payload := first
		if calls == 2 {
			payload = second
		}
		b, _ := json.Marshal(payload)
		return Decode(b)
	}, func(_, raw string) (string, error) { return raw, nil })
	if err != nil || out.Truncated || calls != 2 || len(out.Jobs) != 26 {
		t.Fatal("incomplete Enterprise inventory", err, out.Truncated, calls, len(out.Jobs))
	}
	if out.Jobs[25].URL != "https://hyatt.taleo.net/careersection/1/jobdetail.ftl?job=HKG000001&lang=en" || out.Jobs[25].Title != "Guest Services Agent" {
		t.Fatal("Enterprise canonical identity or title changed", out.Jobs[25])
	}
}
