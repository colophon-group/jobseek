package worker

import (
	"context"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestTaleoEnterpriseLegacyProductionProfileAndCompleteJumeirahListing(t *testing.T) {
	board := "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en"
	config := map[string]string{
		"crawler_type": "taleo", "board_url": board,
		"board_slug": "jumeirah-legacy-fixture", "company_id": "00000000-0000-4000-8000-000000000002",
		"domain": "dubaiholding.taleo.net", "throttle_key": "dubaiholding.taleo.net",
		"check_interval_minutes": "60", "scrape_interval_hours": "24",
		"metadata":              `{"enterprise_section":"https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en","scraper_type":"taleo","scraper_config":{}}`,
		"monitor_needs_browser": "0", "scraper_needs_browser": "0",
	}
	profile, err := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", config)
	if err != nil || profile.Profile != "taleo.listing-urls/v1" || profile.Endpoint != board || !queue.SecondaryMonitorResourceMatches(profile, config, board) || queue.SecondaryMonitorResourceMatches(profile, config, "https://foreign.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en") {
		t.Fatal("Enterprise listing did not receive bounded production authority", profile, err)
	}
	config["board_url"] = "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/jobsearch.ftl?lang=en&portal=321050%E2%80%A6"
	linkedProfile, err := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", config)
	if err != nil || linkedProfile.Endpoint != board || !queue.SecondaryMonitorResourceMatches(linkedProfile, config, board) {
		t.Fatal("official Enterprise search link was not bound to its canonical listing", linkedProfile, err)
	}
	config["board_url"] = board
	fixture, err := os.ReadFile("../../tests/fixtures/taleo_enterprise/jumeirah_listing.html")
	if err != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"complete", "source-short", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.String() != "/careersection/jum_ext_quickapply/joblist.ftl?lang=en" {
					t.Errorf("unexpected Taleo request %s", r.URL)
				}
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
					return
				}
				w.Header().Set("Content-Type", "text/html")
				body := string(fixture)
				if mode == "source-short" {
					body = strings.Replace(body, "19 jobs found", "20 jobs found", 1)
				}
				w.Write([]byte(body))
			}))
			out, err := FetchLinkedInTaleoPracticeMatchHTTP(context.Background(), client.client, profile, config, func(context.Context, time.Duration) error { return nil })
			if mode == "complete" {
				if err != nil || out.Truncated || len(out.Jobs) != 19 {
					t.Fatal("Jumeirah native listing was incomplete", err, out.Truncated, len(out.Jobs))
				}
				if out.Jobs[0].URL != "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/jobdetail.ftl?job=26000037&lang=en" || !out.Jobs[0].URLOnly {
					t.Fatal("canonical detail schedule lost", out.Jobs[0])
				}
			} else if err == nil || len(out.Jobs) != 0 {
				t.Fatal("incomplete or reserved source published a prefix", mode, err, len(out.Jobs))
			}
		})
	}
}
