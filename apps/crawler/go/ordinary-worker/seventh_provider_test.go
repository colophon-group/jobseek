package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSeventhProviderFieldsMatchActualPython(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_seventh_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	d, e := api.Decode(raw)
	if e != nil {
		t.Fatal(e)
	}
	cases := d.Value.(map[string]any)["cases"].([]any)
	if len(cases) != 17 {
		t.Fatal("frozen Python corpus changed")
	}
	for i, c := range cases {
		m := c.(map[string]any)
		provider := m["provider"].(string)
		t.Run(fmt.Sprintf("%s/%d", provider, i), func(t *testing.T) {
			o := api.SeventhProviderOptions{Provider: provider, Slug: "tenant", Origin: "https://tenant.careers.hibob.com"}
			got, e := d.SeventhProviderJobFields(m["row"], o)
			if e != nil {
				t.Fatal(e)
			}
			if got != nil && provider == "hibob" {
				normalizeSeventhLocationType(got)
			}
			if got != nil {
				for _, key := range []string{"url", "title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "language", "extras", "metadata"} {
					if _, ok := got[key]; !ok {
						got[key] = nil
					}
				}
			}
			canonical := func(v any) any {
				raw, e := json.Marshal(v)
				if e != nil {
					t.Fatal(e)
				}
				var out any
				if json.Unmarshal(raw, &out) != nil {
					t.Fatal("invalid field JSON")
				}
				return out
			}
			if !reflect.DeepEqual(canonical(got), canonical(m["expected"])) {
				t.Fatalf("fields differ: got %v want %v", got, m["expected"])
			}
		})
	}
}

func normalizeSeventhLocationType(fields map[string]any) {
	if raw, ok := fields["job_location_type"].(string); ok {
		kind := enrichment.NormalizeJobLocationType(raw)
		fields["job_location_type"] = nil
		if kind != "" {
			fields["job_location_type"] = kind
		}
	}
}

func TestSeventhProviderHTTPPaginationAndPolicy(t *testing.T) {
	for _, provider := range []string{"deel", "hibob", "traffit"} {
		for _, denied := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/denied=%t", provider, denied), func(t *testing.T) {
				config := map[string]string{"crawler_type": provider, "monitor_needs_browser": "0"}
				config["board_url"] = "https://tenant.traffit.com/career/"
				if provider == "deel" {
					config["board_url"] = "https://jobs.deel.com/tenant"
				}
				if provider == "hibob" {
					config["board_url"] = "https://tenant.careers.hibob.com/"
				}
				config["metadata"] = `{"scraper_type":"skip"}`
				o, e := api.SeventhProviderOptionsFromMetadata(provider, config["board_url"], config["metadata"])
				if e != nil {
					t.Fatal(e)
				}
				p := queue.GreenhouseMonitorProfile{Provider: provider, Profile: provider + ".public-items/v1", Endpoint: o.ListingURL()}
				requests := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					if r.Method != "GET" {
						t.Error("unexpected method")
					}
					if denied && requests == 2 || denied && provider == "hibob" {
						w.Header().Set("TDM-Reservation", "1")
					}
					switch provider {
					case "deel":
						if strings.HasSuffix(r.URL.Path, "career_page_settings") {
							fmt.Fprint(w, `{"organizationId":"org","jobBoard":{"id":"board"}}`)
							return
						}
						if r.URL.Path != "/guest/ats/organizations/org/job_boards/board/job_postings" {
							t.Error("wrong resolved endpoint")
						}
						fmt.Fprint(w, `[{"id":"one","title":"Engineer","richtextDescription":"<p>Build</p>"}]`)
					case "hibob":
						if r.Header.Get("Referer") != "https://tenant.careers.hibob.com/" || r.Header.Get("Accept") != "application/json" {
							t.Error("public context headers missing")
						}
						fmt.Fprint(w, `{"jobAdDetails":[{"id":"one","title":"Engineer","description":"<p>Build</p>","workspaceType":"Hybrid"}]}`)
					case "traffit":
						if r.Header.Get("X-Request-Page-Size") != "100" || r.Header.Get("X-Request-Current-Page") != fmt.Sprint(requests) {
							t.Error("pagination headers differ")
						}
						w.Header().Set("X-Result-Total-Pages", "2")
						fmt.Fprintf(w, `[{"url":"https://tenant.traffit.com/job/%d","advert":{"name":"Engineer","values":[{"field_id":"description","value":"<p>Build</p>"}]}}]`, requests)
					}
				}))
				got, e := discoverSeventhProviderInventory(context.Background(), client.client, p, config)
				if denied {
					if e == nil || len(got.Jobs) != 0 {
						t.Fatal("reserved response became inventory")
					}
					return
				}
				want := 1
				if provider == "traffit" {
					want = 2
				}
				if e != nil || len(got.Jobs) != want || got.Truncated {
					t.Fatal("complete inventory failed", e, len(got.Jobs))
				}
				for _, job := range got.Jobs {
					if job.Title == nil || *job.Title != "Engineer" || job.Description == nil || !strings.Contains(*job.Description, "Build") {
						t.Fatal("canonical rich fields lost")
					}
				}
			})
		}
	}
}
