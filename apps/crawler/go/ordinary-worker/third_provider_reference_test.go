package worker

import (
	"context"
	"encoding/json"
	"errors"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"os"
	"reflect"
	"sort"
	"sync"
	"testing"
)

func thirdHTTPCases(t *testing.T) []secondaryHTTPCase {
	t.Helper()
	raw, e := os.ReadFile("testdata/python_third_provider_http.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct{ HTTP []secondaryHTTPCase }
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.HTTP) != 37 {
		t.Fatal("actual Python third-provider HTTP reference missing")
	}
	return corpus.HTTP
}
func TestThirdProvidersMatchActualPythonHTTPRequestsAndOutput(t *testing.T) {
	for _, c := range thirdHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			client := secondaryHTTPFixture(t, c, &requests, &mutex)
			config := map[string]string{"crawler_type": c.Provider, "monitor_needs_browser": "0", "board_url": c.BoardURL, "metadata": string(c.Metadata)}
			profile := "jobvite.listing-urls/v1"
			if c.Provider == "comeet" {
				profile = "comeet.hosted-items/v1"
				if c.BoardURL != c.Endpoint {
					profile = "comeet.api-items/v1"
				}
			}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider, Profile: profile, Endpoint: c.Endpoint}
			var got RichDiscovery
			var e error
			if c.Provider == "comeet" {
				got, e = discoverComeetInventory(context.Background(), client.client, p, config)
			} else {
				got, e = discoverJobviteInventoryWithWait(context.Background(), client.client, p, config, noSecondaryWait)
			}
			var failure *DiscoveryError
			gone := errors.As(e, &failure) && failure.Kind == "provider_gone"
			if (e != nil) != c.Expected.Error || gone != c.Expected.Gone {
				t.Fatalf("failure differs: %v gone=%v", e, gone)
			}
			mutex.Lock()
			observed := append([]secondaryHTTPRequest{}, requests...)
			mutex.Unlock()
			if !reflect.DeepEqual(observed, c.Requests) {
				t.Fatalf("request contract changed: actual=%v expected=%v", observed, c.Requests)
			}
			if e != nil {
				return
			}
			urls := []string{}
			fields := []map[string]any{}
			for _, j := range got.Jobs {
				urls = append(urls, j.URL)
				if j.URLOnly {
					continue
				}
				fields = append(fields, map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "employment_type": j.EmploymentType, "job_location_type": j.JobLocationType, "date_posted": j.DatePosted, "language": j.Language, "metadata": j.Metadata, "extras": j.Extras})
			}
			sort.Strings(urls)
			sort.Slice(fields, func(i, j int) bool { return fields[i]["url"].(string) < fields[j]["url"].(string) })
			raw, _ := json.Marshal(fields)
			var normalized []map[string]any
			json.Unmarshal(raw, &normalized)
			if got.Truncated != c.Expected.Truncated || !reflect.DeepEqual(urls, c.Expected.URLs) || !reflect.DeepEqual(normalized, c.Expected.Jobs) {
				t.Fatalf("inventory changed: actual=%s expected=%v urls=%v", raw, c.Expected.Jobs, urls)
			}
		})
	}
}
