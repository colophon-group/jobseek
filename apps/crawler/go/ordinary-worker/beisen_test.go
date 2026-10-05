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

func TestBeisenDiscoveryMatchesActualPythonRequestsInventoryAndFailures(t *testing.T) {
	data, err := os.ReadFile("testdata/python_beisen.json")
	type request struct{ Method, URL, Body, Cookie string }
	var corpus struct {
		Cases []struct {
			Name     string
			BoardURL string `json:"board_url"`
			Metadata json.RawMessage
			Pages    map[string]json.RawMessage
			Requests []request
			Expected struct {
				Error, Gone, Reserved, Truncated, Hybrid bool
				Jobs                                     []any
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 42 {
		t.Fatal("actual Beisen corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			counts := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				url := "https://" + r.Host + r.URL.String()
				body := make([]byte, r.ContentLength)
				if r.ContentLength > 0 {
					_, _ = r.Body.Read(body)
				}
				requests = append(requests, request{r.Method, url, string(body), r.Header.Get("Cookie")})
				index := counts[url]
				counts[url]++
				raw := c.Pages[url]
				if len(raw) == 0 {
					t.Error("undeclared page URL", url)
					w.WriteHeader(500)
					return
				}
				if raw[0] == '[' {
					var entries []json.RawMessage
					if json.Unmarshal(raw, &entries) != nil {
						t.Fatal("bad retry fixture")
					}
					raw = entries[min(index, len(entries)-1)]
				}
				var response struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(raw, &response) != nil {
					t.Fatal("bad page fixture")
				}
				for k, v := range response.Headers {
					w.Header().Set(k, v)
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.WriteHeader(response.Status)
				fmt.Fprint(w, response.Body)
			}))
			config := map[string]string{"crawler_type": "beisen", "monitor_needs_browser": "0", "metadata": string(c.Metadata), "board_url": c.BoardURL}
			p := queue.GreenhouseMonitorProfile{Provider: "beisen", Profile: "beisen.portal-items/v1", Endpoint: "https://acme-jobs.zhiye.com/"}
			got, err := discoverBeisenInventory(context.Background(), client.client, p, config)
			gone, reserved := false, false
			if got.Response != nil {
				gone = queue.BeisenMonitorPrimaryGone(config, got.Response.endpoint, got.Response.status, got.Response.providerDisabled)
				reserved = got.Response.reserved
			}
			if (err != nil) != c.Expected.Error || gone != c.Expected.Gone || reserved != c.Expected.Reserved || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("request/failure authority differs: error=%v gone=%v reserved=%v requests=%v expected=%v", err, gone, reserved, requests, c.Requests)
			}
			if err != nil {
				return
			}
			rows := []map[string]any{}
			for _, j := range got.Jobs {
				if j.Hybrid != c.Expected.Hybrid {
					t.Fatal("legacy hybrid marker lost")
				}
				rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": nil, "localizations": nil, "base_salary": nil, "source_identity": nil})
			}
			encoded, _ := json.Marshal(rows)
			var jobs []any
			_ = json.Unmarshal(encoded, &jobs)
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(jobs, c.Expected.Jobs) {
				t.Fatalf("inventory differs: truncated=%v/%v rows=%d/%d", got.Truncated, c.Expected.Truncated, len(jobs), len(c.Expected.Jobs))
			}
		})
	}
}
