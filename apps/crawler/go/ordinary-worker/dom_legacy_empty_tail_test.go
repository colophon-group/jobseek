package worker

import (
	"context"
	"io"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestDOMLegacyEmptyMarkerRetainsBoundedAuthoritativeTail(t *testing.T) {
	for _, mode := range []string{"late-jobs", "late-empty", "missing-marker", "body-limit"} {
		t.Run(mode, func(t *testing.T) {
			endpoint := "https://careers.example.com/listing"
			config := map[string]string{"crawler_type": "dom", "board_url": endpoint, "metadata": `{"link_selector":"a.job","empty_selector":".empty","empty_text":"No vacancies","transport_attempts":1,"scraper_type":"skip"}`}
			options, err := queue.DOMMonitorOptions(config)
			if err != nil || options.Document.MaxBodyBytes != 2<<20 {
				t.Fatal("legacy authoritative state lost its original streaming limit", err)
			}
			body := strings.Repeat(" ", 836_000)
			switch mode {
			case "late-jobs":
				body += `<a class="job" href="/jobs/one">Engineer</a>`
			case "late-empty":
				body += `<p class="empty">No vacancies</p>`
			case "missing-marker":
				body += `<p>Other content</p>`
			case "body-limit":
				body = strings.Repeat(" ", 2<<20) + `<p class="empty">No vacancies</p>`
			}
			calls := 0
			client := &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				return &http.Response{StatusCode: 200, Request: r, Header: http.Header{"Content-Type": []string{"text/html"}}, Body: io.NopCloser(strings.NewReader(body))}, nil
			})}
			profile := queue.GreenhouseMonitorProfile{Endpoint: endpoint, Profile: "dom.direct-urls/v1", Provider: "dom"}
			found, err := discoverDOMInventory(context.Background(), client, profile, config)
			if calls != 1 {
				t.Fatal("authoritative read exceeded its configured attempt budget", calls)
			}
			if mode == "missing-marker" || mode == "body-limit" {
				if err == nil || len(found.Jobs) != 0 || found.VerifiedEmptyReason != "" {
					t.Fatal("incomplete state turned into healthy inventory", len(found.Jobs), err)
				}
				return
			}
			if err != nil {
				t.Fatal("authoritative trailing state was truncated", err)
			}
			if mode == "late-jobs" && (len(found.Jobs) != 1 || found.Jobs[0].URL != "https://careers.example.com/jobs/one") {
				t.Fatal("complete trailing job was lost", len(found.Jobs))
			}
			// Original legacy selectors accept the empty set and retain the
			// shared scheduler's normal empty-confirmation policy.
			if mode == "late-empty" && len(found.Jobs) != 0 {
				t.Fatal("authoritative zero was lost", len(found.Jobs))
			}
		})
	}
}
