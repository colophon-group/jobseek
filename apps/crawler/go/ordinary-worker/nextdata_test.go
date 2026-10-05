package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"reflect"
	"sort"
	"sync"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestNextdataStreamMatchesActualPythonChunksRequestsAndFailurePrefixes(t *testing.T) {
	data, err := os.ReadFile("testdata/python_nextdata.json")
	var corpus struct {
		Cases []struct {
			Name     string
			BoardURL string `json:"board_url"`
			Metadata json.RawMessage
			Pages    map[string]json.RawMessage
			Requests []struct{ Method, URL string }
			Expected struct {
				Error  bool
				Chunks []any
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 37 {
		t.Fatal("actual Python NextData stream corpus missing")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []struct{ Method, URL string }{}
			calls := map[string]int{}
			var mu sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				url := "https://" + r.Host + r.URL.String()
				mu.Lock()
				requests = append(requests, struct{ Method, URL string }{r.Method, url})
				index := calls[url]
				calls[url]++
				mu.Unlock()
				body := c.Pages[url]
				if len(body) == 0 {
					t.Error("unexpected page URL", url)
					w.WriteHeader(500)
					return
				}
				if body[0] == '[' {
					var entries []json.RawMessage
					if json.Unmarshal(body, &entries) != nil {
						t.Fatal("bad fixture")
					}
					body = entries[min(index, len(entries)-1)]
				}
				var response struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(body, &response) != nil {
					t.Fatal("bad page fixture")
				}
				for k, v := range response.Headers {
					w.Header().Set(k, v)
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.WriteHeader(response.Status)
				fmt.Fprint(w, response.Body)
			}))
			config := map[string]string{"crawler_type": "nextdata", "monitor_needs_browser": "0", "metadata": string(c.Metadata), "board_url": c.BoardURL}
			p := queue.GreenhouseMonitorProfile{Provider: "nextdata", Profile: "nextdata.embedded-items/v1", Endpoint: c.BoardURL}
			var metadata map[string]any
			_ = json.Unmarshal(c.Metadata, &metadata)
			fields, _ := metadata["fields"].(map[string]any)
			chunks := []any{}
			_, err := discoverNextdataInventory(context.Background(), client.client, p, config, func(jobs []RichMonitorJob) error {
				if len(fields) == 0 {
					unique := map[string]bool{}
					for _, j := range jobs {
						unique[j.URL] = true
					}
					urls := []string{}
					for url := range unique {
						urls = append(urls, url)
					}
					sort.Strings(urls)
					body, _ := json.Marshal(urls)
					var chunk any
					_ = json.Unmarshal(body, &chunk)
					chunks = append(chunks, chunk)
					return nil
				}
				out := []map[string]any{}
				for _, j := range jobs {
					out = append(out, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": nil, "localizations": nil, "base_salary": nil, "source_identity": nullableNextdataIdentity(j.SourceIdentity)})
				}
				body, _ := json.Marshal(out)
				var chunk any
				_ = json.Unmarshal(body, &chunk)
				chunks = append(chunks, chunk)
				return nil
			})
			sort.Slice(requests, func(i, j int) bool {
				if requests[i].URL == requests[j].URL {
					return requests[i].Method < requests[j].Method
				}
				return requests[i].URL < requests[j].URL
			})
			if (err != nil) != c.Expected.Error || !reflect.DeepEqual(requests, c.Requests) || !reflect.DeepEqual(chunks, c.Expected.Chunks) {
				t.Fatalf("stream/request/failure contract differs: err=%v; chunks=%d/%d; requests=%v / %v", err, len(chunks), len(c.Expected.Chunks), requests, c.Requests)
			}
		})
	}
}

func nullableNextdataIdentity(value string) any {
	if value == "" {
		return nil
	}
	return value
}
