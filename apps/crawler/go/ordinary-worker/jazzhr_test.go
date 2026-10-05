package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestJazzHRMatchesActualPythonInventoryAndRequests(t *testing.T) {
	data, err := os.ReadFile("testdata/python_jazzhr.json")
	var corpus struct {
		Cases []struct {
			Name, Body string
			Status     int
			Headers    map[string]string
			Requests   []struct{ Method, URL string }
			Expected   struct {
				Error bool
				URLs  []string
			}
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 7 {
		t.Fatal("actual Python corpus unavailable")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			requests := []struct{ Method, URL string }{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests = append(requests, struct{ Method, URL string }{r.Method, "https://" + r.Host + r.URL.String()})
				for k, v := range c.Headers {
					w.Header().Set(k, v)
				}
				w.WriteHeader(c.Status)
				fmt.Fprint(w, c.Body)
			}))
			p := queue.GreenhouseMonitorProfile{Provider: "jazzhr", Profile: "jazzhr.listing-urls/v1", Token: "fixture", Endpoint: "https://fixture.applytojob.com/apply/jobs"}
			got, e := discoverJazzHRInventory(context.Background(), client.client, p)
			if (e != nil) != c.Expected.Error || !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("inventory rejection or request chain differs", e, requests, c.Requests)
			}
			if !c.Expected.Error {
				urls := []string{}
				for _, j := range got.Jobs {
					urls = append(urls, j.URL)
				}
				if !reflect.DeepEqual(urls, c.Expected.URLs) || got.Truncated {
					t.Fatal("canonical inventory differs", urls, c.Expected.URLs)
				}
			}
		})
	}
}

func TestJazzHRPreservesUnicodeHTMLAndSortedInventoryCaps(t *testing.T) {
	p := queue.GreenhouseMonitorProfile{Token: "fixture", Endpoint: "https://fixture.applytojob.com/apply/jobs"}
	for _, mode := range []string{"html", "jobs"} {
		source := `<div id="job_listings_wrapper">`
		if mode == "html" {
			source += strings.Repeat("界", 5_000_000-len(source))
		} else {
			var b strings.Builder
			b.WriteString(source)
			for i := 50_000; i >= 0; i-- {
				fmt.Fprintf(&b, `<a href="/apply/jobs/details/%05d">Job</a>`, i)
			}
			source = b.String()
		}
		result, err := parseJazzHRInventory(context.Background(), RichDiscovery{Jobs: []RichMonitorJob{}}, p, source)
		if err != nil || !result.Truncated || mode == "jobs" && (len(result.Jobs) != 50_000 || !strings.HasSuffix(result.Jobs[0].URL, "00000") || !strings.HasSuffix(result.Jobs[49_999].URL, "49999")) {
			t.Fatal("existing decoded/inventory caps differ", err)
		}
	}
}
