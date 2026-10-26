package worker

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"sync"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

// Protected complete captures execute the original wrapper, including configured
// URL rewriting and job filters. Replay keeps real provider requests bound while
// comparing every returned public posting without printing its private identity.
func TestCatalogueURLListingOriginalPublicCaptures(t *testing.T) {
	dir := os.Getenv("JOBSEEK_CATALOGUE_URL_LISTING_CAPTURE_DIR")
	if dir == "" {
		t.Skip("protected complete public references required")
	}
	for _, provider := range []string{"dom", "workday", "lever"} {
		t.Run(provider, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(dir, provider+"-original.private.json"))
			var c struct {
				Provider, ID, Status string
				Config               map[string]string
				Truncated            bool
				Jobs                 []map[string]any
				Exchanges            []struct {
					Method, URL, Body string
					Status            int
					RequestBody       string            `json:"request_body"`
					ResponseHeaders   map[string]string `json:"response_headers"`
				}
			}
			if err != nil || json.Unmarshal(raw, &c) != nil || c.Provider != provider || c.Status != "complete" || c.Truncated || len(c.Jobs) == 0 || len(c.Exchanges) == 0 {
				t.Fatal("complete original public reference unavailable")
			}
			p, err := queue.InspectRichMonitor(c.ID, c.Config)
			if err != nil {
				t.Fatal("actual canonical configuration unsupported")
			}
			used := make([]bool, len(c.Exchanges))
			calls := 0
			var mu sync.Mutex
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				defer mu.Unlock()
				body, readErr := io.ReadAll(r.Body)
				if readErr != nil {
					t.Error("request body unavailable")
					w.WriteHeader(400)
					return
				}
				normalized := func(raw string) any {
					if raw == "" {
						return nil
					}
					var v any
					if json.Unmarshal([]byte(raw), &v) != nil {
						return raw
					}
					return v
				}
				index := -1
				for n, x := range c.Exchanges {
					u, e := url.Parse(x.URL)
					if !used[n] && e == nil && r.Method == x.Method && r.Host == u.Host && r.URL.EscapedPath() == u.EscapedPath() && r.URL.Query().Encode() == u.Query().Encode() && reflect.DeepEqual(normalized(string(body)), normalized(x.RequestBody)) {
						index = n
						break
					}
				}
				if index < 0 {
					if trace := os.Getenv("JOBSEEK_CATALOGUE_URL_LISTING_TRACE_DIR"); trace != "" {
						f, e := os.OpenFile(filepath.Join(trace, provider+"-native-request-mismatch1.private.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
						if e == nil {
							json.NewEncoder(f).Encode(map[string]any{"method": r.Method, "url": "https://" + r.Host + r.URL.RequestURI(), "request_body": string(body)})
							f.Sync()
							f.Close()
						}
					}

					t.Error("native request absent from original complete capture")
					w.WriteHeader(400)
					return
				}
				used[index] = true
				calls++
				x := c.Exchanges[index]
				for k, v := range x.ResponseHeaders {
					w.Header().Set(k, v)
				}
				w.WriteHeader(x.Status)
				io.WriteString(w, x.Body)
			}))
			ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
			defer cancel()
			var result RichDiscovery
			switch provider {
			case "dom":
				result, err = discoverDOMInventory(ctx, client.client, p, c.Config)
			case "lever":
				result, err = DiscoverRichMonitor(ctx, client.client, p)
			case "workday":
				var md map[string]any
				json.Unmarshal([]byte(c.Config["metadata"]), &md)
				options, e := workday.ParseInventoryConfig(c.Config["board_url"], md)
				if e != nil {
					t.Fatal("actual Workday inventory options unsupported")
				}
				found, e := DiscoverWorkdayInventory(ctx, client, options)
				err = e
				result.Truncated = found.Truncated
				for _, source := range found.URLs {
					result.Jobs = append(result.Jobs, RichMonitorJob{URL: source})
				}
			}
			if err != nil || result.Truncated {
				t.Fatal("native complete public inventory failed")
			}
			if provider == "dom" || provider == "workday" {
				result.Jobs, err = applyFeedMonitorURLs(ctx, c.Config, result.Jobs)
				if err != nil {
					t.Fatal("original canonical URL/filter policy failed")
				}
			}
			actual := []map[string]any{}
			for _, job := range result.Jobs {
				row := map[string]any{"url": job.URL}
				if provider != "workday" {
					row = map[string]any{"url": job.URL, "title": job.Title, "description": job.Description, "locations": job.Locations, "date_posted": job.DatePosted, "base_salary": job.BaseSalary, "language": job.Language, "metadata": job.Metadata, "extras": job.Extras, "employment_type": job.EmploymentType, "job_location_type": job.JobLocationType}
				}
				b, _ := json.Marshal(row)
				var normalized map[string]any
				json.Unmarshal(b, &normalized)
				actual = append(actual, normalized)
			}
			clean := func(jobs []map[string]any) {
				for _, job := range jobs {
					for k, v := range job {
						if v == nil {
							delete(job, k)
						} else if (k == "metadata" || k == "extras") && len(v.(map[string]any)) == 0 {
							delete(job, k)
						}
					}
				}
				sort.Slice(jobs, func(i, j int) bool { return jobs[i]["url"].(string) < jobs[j]["url"].(string) })
			}
			clean(c.Jobs)
			clean(actual)
			if len(actual) != len(c.Jobs) || calls != len(c.Exchanges) {
				t.Fatal("complete posting/request count changed", len(actual), len(c.Jobs), calls, len(c.Exchanges))
			}
			for n, row := range actual {
				if !reflect.DeepEqual(row, c.Jobs[n]) {
					keys := []string{}
					for k, v := range row {
						if !reflect.DeepEqual(v, c.Jobs[n][k]) {
							keys = append(keys, k)
						}
					}
					for k := range c.Jobs[n] {
						if _, ok := row[k]; !ok {
							keys = append(keys, k)
						}
					}
					sort.Strings(keys)
					t.Fatal("original public posting fields changed", n, keys)
				}
			}
			t.Logf("original complete public inventory matched: %d postings, %d requests", len(actual), calls)
		})
	}
}
