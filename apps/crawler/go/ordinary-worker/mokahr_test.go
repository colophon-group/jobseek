package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"reflect"
	"sync"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestMokahrCanonicalFragmentIdentityMatchesActualPythonPipeline(t *testing.T) {
	var corpus struct {
		URLIdentity []struct {
			Board, Source, Reason string
			Sources, Accepted     []string
			Dropped               map[string]int
		} `json:"url_identity"`
	}
	body, e := os.ReadFile("testdata/python_mokahr.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.URLIdentity) != 15 {
		t.Fatal("actual Python fragment identity reference missing")
	}
	for _, c := range corpus.URLIdentity {
		if c.Source != "" {
			if classifyJobURL(c.Source, c.Board) != c.Reason {
				t.Fatal("non-posting homepage guard changed")
			}
			continue
		}
		jobs := []RichMonitorJob{}
		for _, source := range c.Sources {
			jobs = append(jobs, RichMonitorJob{URL: source})
		}
		got, e := NormalizeRichInventory(context.Background(), c.Board, jobs, false)
		if e != nil {
			t.Fatal(e)
		}
		urls := []string{}
		for _, job := range got.Jobs {
			urls = append(urls, job.URL)
		}
		if !reflect.DeepEqual(urls, c.Accepted) || !reflect.DeepEqual(got.DropReasons, c.Dropped) || got.Discovered != len(c.Sources) {
			t.Fatal("MokaHR fragments collapsed or were dropped as listing links")
		}
	}
}

func TestMokahrDiscoveryMatchesActualPythonHTTPInventoriesAndFailures(t *testing.T) {
	type request struct {
		Method, URL, Cookie string
		Body                any
	}
	var corpus struct {
		HTTP []struct {
			Name     string
			BoardURL string `json:"board_url"`
			Metadata json.RawMessage
			Pages    map[string]json.RawMessage
			Requests []request
			Expected struct {
				Error, Gone, Truncated bool
				Jobs                   []map[string]any
			}
		}
	}
	body, e := os.ReadFile("testdata/python_mokahr.json")
	if e != nil || json.Unmarshal(body, &corpus) != nil || len(corpus.HTTP) != 28 {
		t.Fatal("actual Python HTTP corpus missing")
	}
	for _, c := range corpus.HTTP {
		t.Run(c.Name, func(t *testing.T) {
			requests := []request{}
			counts := map[string]int{}
			var mutex sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				url := "https://" + r.Host + r.URL.String()
				body, e := io.ReadAll(r.Body)
				if e != nil {
					t.Error("fixture request body failed")
					w.WriteHeader(500)
					return
				}
				var value any
				if len(body) > 0 && json.Unmarshal(body, &value) != nil {
					t.Error("invalid request JSON")
					w.WriteHeader(500)
					return
				}
				mutex.Lock()
				requests = append(requests, request{r.Method, url, r.Header.Get("Cookie"), value})
				index := counts[url]
				counts[url]++
				mutex.Unlock()
				raw := c.Pages[url]
				if len(raw) == 0 {
					t.Error("undeclared resource", url)
					w.WriteHeader(500)
					return
				}
				if raw[0] == '[' {
					var rows []json.RawMessage
					if json.Unmarshal(raw, &rows) != nil || len(rows) == 0 {
						t.Error("bad sequence fixture")
						w.WriteHeader(500)
						return
					}
					raw = rows[min(index, len(rows)-1)]
				}
				var response struct {
					Body    string
					Status  int
					Headers map[string]string
				}
				if json.Unmarshal(raw, &response) != nil {
					t.Error("bad resource fixture")
					w.WriteHeader(500)
					return
				}
				for k, v := range response.Headers {
					w.Header().Set(k, v)
				}
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				w.WriteHeader(response.Status)
				fmt.Fprint(w, response.Body)
			}))
			config := map[string]string{"crawler_type": "mokahr", "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			profile := queue.GreenhouseMonitorProfile{Provider: "mokahr", Profile: "mokahr.encrypted-items/v1", Endpoint: c.BoardURL}
			got, e := discoverMokahrInventory(context.Background(), client.client, profile, config)
			var failure *DiscoveryError
			gone := errors.As(e, &failure) && failure.Kind == "provider_gone"
			if (e != nil) != c.Expected.Error || gone != c.Expected.Gone {
				t.Fatalf("failure authority differs: error=%v gone=%v; reference error=%v gone=%v", e, gone, c.Expected.Error, c.Expected.Gone)
			}
			group := func(rows []request) map[string][]request {
				out := map[string][]request{}
				for _, r := range rows {
					out[r.URL] = append(out[r.URL], r)
				}
				return out
			}
			mutex.Lock()
			observed := group(requests)
			mutex.Unlock()
			expected := group(c.Requests)
			if c.Name == "partition-failure-discards-prefix" {
				// Cancelled sibling work may stop before the legacy gather finishes.
				// Every emitted request must still be an exact reference prefix.
				for endpoint, rows := range observed {
					ref := expected[endpoint]
					if len(rows) > len(ref) || !reflect.DeepEqual(rows, ref[:len(rows)]) {
						t.Fatal("cancelled partition emitted a non-reference request")
					}
				}
			} else if !reflect.DeepEqual(observed, expected) {
				t.Fatalf("request history differs: actual=%v expected=%v", observed, expected)
			}
			if e != nil {
				if len(got.Jobs) != 0 {
					t.Fatal("failed prefix exposed as inventory")
				}
				return
			}
			rows := []map[string]any{}
			for _, j := range got.Jobs {
				rows = append(rows, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "metadata": j.Metadata, "language": j.Language, "extras": j.Extras, "localizations": nil, "source_identity": nil})
			}
			// Rich canonical preparation derives salary from the description. The
			// pure parser corpus separately verifies every structured salary field.
			for _, j := range c.Expected.Jobs {
				delete(j, "base_salary")
			}
			encoded, _ := json.Marshal(rows)
			var normalized []map[string]any
			if json.Unmarshal(encoded, &normalized) != nil {
				t.Fatal("bad native projection")
			}
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("inventory differs: native rows=%d, reference rows=%d", len(rows), len(c.Expected.Jobs))
			}
		})
	}
}
