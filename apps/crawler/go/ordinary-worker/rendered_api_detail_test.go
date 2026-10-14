package worker

import (
	"context"
	"encoding/json"
	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealBrowserAPIDetailWritesAndSettlesOriginalBrowserQueue(t *testing.T) {
	for _, mode := range []string{"success", "reserved", "failed", "bad-fingerprint"} {
		t.Run(mode, func(t *testing.T) {
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"api_sniffer","scraper_config":{"fields":{"title":"title","description":"description","locations":"locations"}}}`, "", queue.Browser)
			if !a.RequiresRenderedDetails() {
				t.Fatal("renderer dependency missing")
			}
			renderer := heldRenderedDetail(func(_ context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveBoardSHA256, Outcome: "success", Detail: json.RawMessage(`{"title":"Native API Engineer","description":"<p>Complete original browser API description.</p>","locations":["Zurich"]}`)}
				switch mode {
				case "reserved":
					response.Outcome = "publisher_reserved"
					response.Detail = nil
					response.Reservation = &replay.Reservation{URL: "https://api.example.net/job", Source: "header"}
				case "failed":
					response.Outcome = "failed"
					response.Detail = nil
				case "bad-fingerprint":
					response.ConfigFingerprint = strings.Repeat("0", 64)
				}
				return parseAPIDetailCapture(p, response)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("browser API detail issued direct request") })
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(context.Background(), a, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			if err != nil || !result.Settled {
				t.Fatal("browser detail did not settle", err)
			}
			var title string
			var due *time.Time
			var reserved bool
			var leases int
			if err := f.pg.QueryRow(context.Background(), `SELECT coalesce(titles[1],''),next_scrape_at,tdm_reserved,(CASE WHEN leased_until IS NULL THEN 0 ELSE 1 END) FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&title, &due, &reserved, &leases); err != nil {
				t.Fatal(err)
			}
			if leases != 0 || f.r.ZCard(context.Background(), "inflight:browser").Val() != 0 {
				t.Fatal("browser lease retained")
			}
			if mode == "success" {
				var html string
				if err := f.pg.QueryRow(context.Background(), `SELECT html FROM descriptions WHERE posting_id=$1::uuid`, f.original).Scan(&html); err != nil || title != "Native API Engineer" || !strings.Contains(html, "Complete original browser API description") {
					t.Fatal("canonical fields differ", err)
				}
			}
			if mode == "reserved" {
				if !reserved || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("publisher reservation lost")
				}
			}
			{
				if due == nil {
					t.Fatal("recurring deadline absent")
				}
				score, err := f.r.ZScore(context.Background(), "scrapes_browser:example.com", f.original).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("canonical/Redis deadline differs", err)
				}
			}
			if mode != "success" && title == "Native API Engineer" {
				t.Fatal("failure published detail")
			}
		})
	}
}
