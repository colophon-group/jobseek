package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestTalentBrewTransientStatusRetriesAreBoundedAndCannotPublishPartialInventory(t *testing.T) {
	for _, status := range []int{202, 401, 403} {
		for _, exhausted := range []bool{false, true} {
			t.Run(fmt.Sprintf("status=%d/exhausted=%v", status, exhausted), func(t *testing.T) {
				calls, waits := 0, 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if exhausted || calls < 3 {
						w.WriteHeader(status)
						return
					}
					fmt.Fprint(w, `<div id="search-results" data-total-results="1"></div><div id="search-results-list"><a href="/job/one">One</a></div>`)
				}))
				p := queue.GreenhouseMonitorProfile{Provider: "talentbrew", Profile: "talentbrew.listing-urls/v1", Endpoint: "https://example.com/search-jobs"}
				result, err := FetchTalentBrewHTTP(context.Background(), client.client, p, map[string]string{"board_url": p.Endpoint, "metadata": `{"ajax":false}`, "monitor_needs_browser": "0"}, func(context.Context, time.Duration) error { waits++; return nil })
				if calls != 3 || waits != 2 || exhausted && (err == nil || len(result.Jobs) != 0) || !exhausted && (err != nil || len(result.Jobs) != 1 || result.Jobs[0].URL != "https://example.com/job/one") {
					t.Fatal("retry or complete inventory contract changed", calls, waits, err, result)
				}
			})
		}
	}
}
