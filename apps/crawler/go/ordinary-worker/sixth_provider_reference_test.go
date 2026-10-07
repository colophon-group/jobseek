package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"sort"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestSixthProviderWorkerMatchesActualPython(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_sixth_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Inventories []struct {
			Provider, Scenario string
			Pages              map[string]string
			Requests           []string
			Expected           struct {
				Error, Truncated bool
				URLs             []string
			}
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.Inventories) != 10 {
		t.Fatal("actual Python inventory corpus unavailable")
	}
	normalize := func(source string) string {
		u, e := url.Parse(source)
		if e != nil {
			t.Fatal(e)
		}
		u.RawQuery = u.Query().Encode()
		return u.String()
	}
	for _, c := range corpus.Inventories {
		t.Run(c.Provider+"/"+c.Scenario, func(t *testing.T) {
			pages := map[string]string{}
			for source, body := range c.Pages {
				pages[normalize(source)] = body
			}
			requests := []string{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != "GET" || r.Header.Get("Proxy-Authorization") != "" {
					t.Error("provider request differs")
				}
				source := normalize("https://" + r.Host + r.URL.String())
				requests = append(requests, source)
				body, ok := pages[source]
				if !ok {
					t.Error("request left frozen scope", source)
					w.WriteHeader(500)
					return
				}
				fmt.Fprint(w, body)
			}))
			config := map[string]string{"crawler_type": c.Provider, "metadata": "{}", "monitor_needs_browser": "0"}
			p := queue.GreenhouseMonitorProfile{Provider: c.Provider}
			var result RichDiscovery
			var failure error
			if c.Provider == "manatal" {
				config["board_url"] = "https://www.careers-page.com/tenant"
				o, e := api.ManatalOptionsFromMetadata(config["board_url"], "{}")
				if e != nil {
					t.Fatal(e)
				}
				p.Profile, p.Endpoint = "manatal.career-items/v1", o.ListingURL(1)
				result, failure = discoverManatalInventory(context.Background(), client.client, p, config)
			} else {
				config["board_url"] = "https://hrmos.co/pages/tenant/jobs"
				o, e := api.HRMOSOptionsFromMetadata(config["board_url"], "{}")
				if e != nil {
					t.Fatal(e)
				}
				p.Profile, p.Endpoint = "hrmos.listing-urls/v1", o.ListingURL(1)
				result, failure = discoverHRMOSInventoryWithWait(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			}
			if (failure != nil) != c.Expected.Error {
				t.Fatalf("failure differs: %v expected%+v", failure, c.Expected)
			}
			if failure != nil && len(result.Jobs) > 0 {
				t.Fatal("partial jobs escaped failed inventory")
			}
			urls := []string{}
			for _, job := range result.Jobs {
				urls = append(urls, job.URL)
				if c.Provider == "hrmos" && !job.URLOnly {
					t.Fatal("URL-only listing became rich")
				}
			}
			sort.Strings(urls)
			if failure == nil && (!reflect.DeepEqual(urls, c.Expected.URLs) || result.Truncated != c.Expected.Truncated) {
				t.Fatalf("worker inventory differs: %v truncated%v expected%+v", urls, result.Truncated, c.Expected)
			}
			want := []string{}
			for _, source := range c.Requests {
				want = append(want, normalize(source))
			}
			if !reflect.DeepEqual(requests, want) {
				t.Fatalf("requests differ: got%v want%v", requests, want)
			}
		})
	}
}
