package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sort"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestEightfoldHybridMonitorMatchesActualPythonHTTP(t *testing.T) {
	type request struct{ Method, URL string }
	var corpus struct {
		Monitor []struct {
			Name, Now      string
			BoardURL       string `json:"board_url"`
			Metadata       json.RawMessage
			Pages          map[string]json.RawMessage
			Requests       []request
			NativeReserved bool `json:"native_reserved"`
			Expected       struct {
				URLs  []string
				Rich  map[string]map[string]any
				Patch map[string]any
				Error string
			}
		}
	}
	body, e := os.ReadFile("testdata/python_eightfold.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.Monitor) != 27 {
		t.Fatal("actual Python monitor reference missing")
	}
	for _, c := range corpus.Monitor {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			counts := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := "https://" + r.Host + r.URL.String()
				requests = append(requests, request{r.Method, source})
				raw, ok := c.Pages[source]
				if !ok {
					t.Error("undeclared resource", source)
					w.WriteHeader(500)
					return
				}
				index := counts[source]
				counts[source]++
				if len(raw) > 0 && raw[0] == '[' {
					var rows []json.RawMessage
					if json.Unmarshal(raw, &rows) != nil || len(rows) == 0 {
						t.Error("invalid sequence")
						w.WriteHeader(500)
						return
					}
					raw = rows[min(index, len(rows)-1)]
				}
				var row struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(raw, &row) != nil {
					t.Error("invalid resource")
					w.WriteHeader(500)
					return
				}
				for key, value := range row.Headers {
					w.Header().Set(key, value)
				}
				w.Header().Set("Content-Type", "application/xml")
				w.WriteHeader(row.Status)
				fmt.Fprint(w, row.Body)
			}))
			config := map[string]string{"crawler_type": "eightfold", "board_url": c.BoardURL, "metadata": string(c.Metadata), "monitor_needs_browser": "0"}
			profile := queue.GreenhouseMonitorProfile{Provider: "eightfold", Profile: "eightfold.pcsx-sitemap/v1", Endpoint: c.BoardURL + "/careers/sitemap.xml"}
			now, e := time.Parse(time.RFC3339Nano, c.Now)
			if e != nil {
				t.Fatal(e)
			}
			// Both executions skip sleeps; request count/order still proves retry and pagination budgets.
			got, e := discoverEightfoldInventoryAt(context.Background(), client.client, profile, config, now, func(context.Context, time.Duration) error { return nil })
			if c.NativeReserved {
				if e == nil || got.Response == nil || !got.Response.reserved || len(got.Jobs) > 0 {
					t.Fatal("publisher reservation escaped as inventory", e)
				}
				if len(requests) > len(c.Requests) || !reflect.DeepEqual(requests, c.Requests[:len(requests)]) {
					t.Fatal("reserved route emitted non-reference requests")
				}
				return
			}
			if e != nil || c.Expected.Error != "" {
				t.Fatal("failure differs", e, c.Expected.Error)
			}
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatalf("request history differs: got=%v expected=%v", requests, c.Requests)
			}
			urls := []string{}
			rich := map[string]map[string]any{}
			for _, j := range got.Jobs {
				urls = append(urls, j.URL)
				if j.URLOnly {
					continue
				}
				rich[j.URL] = map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "base_salary": nil, "language": j.Language, "localizations": nil, "extras": j.Extras, "metadata": j.Metadata, "source_identity": nil}
				if !j.Hybrid {
					t.Fatal("partial PCSX data could overwrite touched content")
				}
			}
			sort.Strings(urls)
			if !reflect.DeepEqual(urls, c.Expected.URLs) {
				t.Fatal("authoritative sitemap inventory differs")
			}
			b, e := json.Marshal(map[string]any{"rich": rich, "patch": got.MetadataUpdates})
			if e != nil {
				t.Fatal(e)
			}
			var normalized map[string]any
			if json.Unmarshal(b, &normalized) != nil {
				t.Fatal("invalid native values")
			}
			expected, e := json.Marshal(map[string]any{"rich": c.Expected.Rich, "patch": c.Expected.Patch})
			if e != nil {
				t.Fatal(e)
			}
			var reference map[string]any
			if json.Unmarshal(expected, &reference) != nil || !reflect.DeepEqual(normalized, reference) {
				t.Fatalf("hybrid fields/watermark differ: got=%v expected=%v", normalized, reference)
			}
		})
	}
}
