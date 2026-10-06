package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestInlineTransportPreservesPythonInventoriesAndInstalledPublicHeaderBoundary(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inline_transport.json")
	type request struct{ URL, Cookie, Accept string }
	var corpus struct {
		Cases []struct {
			Name     string
			BoardURL string `json:"board_url"`
			Metadata json.RawMessage
			Pages    map[string]json.RawMessage
			Requests []request
			Expected struct {
				Error, Truncated bool
				Jobs             []any
			}
		}
	}
	if err != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Cases) != 20 {
		t.Fatal("actual Inline transport corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			counts := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				endpoint := "https://" + r.Host + r.URL.String()
				requests = append(requests, request{endpoint, r.Header.Get("Cookie"), r.Header.Get("Accept")})
				i := counts[endpoint]
				counts[endpoint]++
				raw := c.Pages[endpoint]
				if len(raw) == 0 {
					t.Error("undeclared request")
					w.WriteHeader(500)
					return
				}
				if raw[0] == '[' {
					var a []json.RawMessage
					if json.Unmarshal(raw, &a) != nil || len(a) == 0 {
						t.Error("invalid retry fixture")
						w.WriteHeader(500)
						return
					}
					raw = a[min(i, len(a)-1)]
				}
				var page struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(raw, &page) != nil {
					t.Error("invalid response fixture")
					w.WriteHeader(500)
					return
				}
				for k, v := range page.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(page.Status)
				fmt.Fprint(w, page.Body)
			}))
			config := map[string]string{"crawler_type": "inline", "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			p := queue.GreenhouseMonitorProfile{Provider: "inline", Profile: "inline.document-items/v1", Endpoint: c.BoardURL}
			got, err := discoverInlineInventory(context.Background(), client.client, p, config)
			// Header-configured fetches keep the installed native public_get boundary:
			// no state cookies and no request to another origin. The frozen actual
			// legacy Inline implementation forwards both; that behavior is not adopted.
			expectedRequests := c.Requests
			if c.Name == "public-header-same-origin-redirect" {
				expectedRequests = append([]request(nil), c.Requests...)
				for i := range expectedRequests {
					expectedRequests[i].Cookie = ""
				}
			}
			if c.Name == "public-header-foreign-refusal" {
				expectedRequests = []request{}
				for _, r := range c.Requests {
					if r.URL == c.BoardURL {
						expectedRequests = append(expectedRequests, r)
					}
				}
			}
			if (err != nil) != c.Expected.Error || !reflect.DeepEqual(requests, expectedRequests) {
				t.Fatalf("Inline transport differs: err=%v requests=%v expected=%v", err, requests, expectedRequests)
			}
			if err != nil {
				return
			}
			rows := []map[string]any{}
			for _, j := range got.Jobs {
				rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "extras": j.Extras})
			}
			encoded, _ := json.Marshal(rows)
			var jobs []any
			_ = json.Unmarshal(encoded, &jobs)
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(jobs, c.Expected.Jobs) {
				t.Fatal("fallback document fields differ")
			}
		})
	}
}
