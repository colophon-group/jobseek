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
			Provider, Scenario, Source string
			Metadata                   map[string]any
			Pages                      map[string]string
			Statuses                   map[string]int
			Requests                   []string
			Expected                   struct {
				Error, Truncated, Gone bool
				URLs                   []string
			}
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.Inventories) != 33 {
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
			statuses := map[string]int{}
			for source, status := range c.Statuses {
				statuses[normalize(source)] = status
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
				if status := statuses[source]; status != 0 {
					w.WriteHeader(status)
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
			} else if c.Provider == "recruiterbox" {
				config["board_url"] = "https://tenant.recruiterbox.com/"
				o, e := api.RecruiterboxOptionsFromMetadata(config["board_url"], "{}")
				if e != nil {
					t.Fatal(e)
				}
				p.Profile, p.Endpoint = "recruiterbox.listing-urls/v1", o.PageURL(1)
				result, failure = discoverRecruiterboxInventoryWithWait(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
			} else if c.Provider == "jobs_ch" {
				config["board_url"] = c.Source
				md, _ := json.Marshal(c.Metadata)
				config["metadata"] = string(md)
				o, e := api.JobCloudOptionsFromMetadata(config["board_url"], config["metadata"])
				if e != nil {
					t.Fatal(e)
				}
				p.Profile, p.Endpoint = "jobs_ch.company-urls/v1", o.SearchURL(1)
				result, failure = discoverJobCloudInventoryWithWait(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { return nil })
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
				if c.Provider != "manatal" && !job.URLOnly {
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
