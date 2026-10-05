package worker

import (
	"context"
	"encoding/json"
	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestBreezyGemMatchesPythonRequestsAndCanonicalFields(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_breezy_gem.json")
	if err != nil {
		t.Fatal(err)
	}
	type request struct{ Method, URL string }
	var corpus struct {
		Cases []struct {
			Name, Provider, Endpoint string
			Payload                  json.RawMessage
			Requests                 []request
			Expected                 struct {
				Error bool
				URLs  []string
				Jobs  []map[string]any
			}
		}
		LocationTypes []struct {
			Raw      string
			Expected *string
		} `json:"location_types"`
	}
	if json.Unmarshal(raw, &corpus) != nil {
		t.Fatal("invalid Python corpus")
	}
	for _, v := range corpus.LocationTypes {
		got := enrichment.NormalizeJobLocationType(v.Raw)
		want := ""
		if v.Expected != nil {
			want = *v.Expected
		}
		if got != want {
			t.Fatal("Python location type differs", v.Raw, got, want)
		}
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, request{r.Method, "https://" + r.Host + r.URL.String()})
				w.Header().Set("Content-Type", "application/json")
				w.Write(c.Payload)
			}))
			result, err := discoverBreezyGemInventory(context.Background(), client.client, queue.GreenhouseMonitorProfile{Provider: c.Provider, Endpoint: c.Endpoint})
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("request chain differs", requests, c.Requests)
			}
			if c.Expected.Error {
				if err == nil {
					t.Fatal("Python failed but Go accepted")
				}
				return
			}
			if err != nil || result.Truncated {
				t.Fatal(result, err)
			}
			if c.Provider == "breezy" {
				unique := map[string]bool{}
				for _, j := range result.Jobs {
					unique[j.URL] = true
				}
				urls := []string{}
				for u := range unique {
					urls = append(urls, u)
				}
				sort.Strings(urls)
				if !reflect.DeepEqual(urls, c.Expected.URLs) {
					t.Fatal("URL inventory differs", urls, c.Expected.URLs)
				}
				return
			}
			if len(result.Jobs) != len(c.Expected.Jobs) {
				t.Fatal("job count differs", len(result.Jobs), len(c.Expected.Jobs))
			}
			for i, j := range result.Jobs {
				actual := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata}
				b, _ := json.Marshal(actual)
				json.Unmarshal(b, &actual)
				for k, v := range actual {
					if !reflect.DeepEqual(v, c.Expected.Jobs[i][k]) {
						t.Fatal("Gem field differs", i, k, v, c.Expected.Jobs[i][k])
					}
				}
			}
		})
	}
}

func TestBreezyGemTruncationUsesExistingProviderInventoryCount(t *testing.T) {
	for _, provider := range []string{"breezy", "gem"} {
		t.Run(provider, func(t *testing.T) {
			rows := make([]any, 50_001)
			for i := range rows {
				if provider == "breezy" {
					rows[i] = map[string]any{"friendly_id": "one"}
				} else {
					rows[i] = map[string]any{"absolute_url": "https://jobs.gem.com/fixture/one"}
				}
			}
			body, _ := json.Marshal(rows)
			jobs, truncated, err := parseBreezyGemInventory(body, queue.GreenhouseMonitorProfile{Provider: provider, Endpoint: "https://fixture.breezy.hr/json"})
			if err != nil || !truncated || len(jobs) != len(rows) {
				t.Fatal("partial source inventory lost truncation marker", len(jobs), truncated, err)
			}
			for i := range rows {
				rows[i] = map[string]any{}
			}
			body, _ = json.Marshal(rows)
			jobs, truncated, err = parseBreezyGemInventory(body, queue.GreenhouseMonitorProfile{Provider: provider, Endpoint: "https://fixture.breezy.hr/json"})
			if err != nil || len(jobs) != 0 || truncated != (provider == "breezy") {
				t.Fatal("Breezy opening/Gem valid job count differs", len(jobs), truncated, err)
			}
		})
	}
}

func TestBreezyGemRejectTrailingJSONBeforeInventory(t *testing.T) {
	for _, provider := range []string{"breezy", "gem"} {
		for _, body := range []string{"[] garbage", "[][]", "[] null"} {
			if _, _, err := parseBreezyGemInventory([]byte(body), queue.GreenhouseMonitorProfile{Provider: provider}); err == nil {
				t.Fatal("malformed JSON inventory accepted", provider, body)
			}
		}
	}
}
