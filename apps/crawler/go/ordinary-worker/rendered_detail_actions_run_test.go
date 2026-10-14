package worker

import (
	"context"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealRenderedActionsDetailPersistsThroughBrowserAuthority(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata"} {
		t.Run(scraper, func(t *testing.T) {
			parser := `{"render":true,"actions":[{"action":"wait","ms":0},{"action":"click","selector":"#details","required":true}],"defaults":{"language":"en"}}`
			html := nativeJSONLDHTML
			if scraper == "dom" {
				parser = `{"render":true,"actions":[{"action":"wait","ms":0},{"action":"click","selector":"#details","required":true}],"steps":[{"tag":"h1","field":"title"},{"tag":"p","attr":"data-field=location","field":"location"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}],"defaults":{"employment_type":"FULL_TIME","language":"en"}}`
				html = nativeDOMHTML
			}
			if scraper == "nextdata" {
				parser = `{"render":true,"actions":[{"action":"wait","ms":0},{"action":"click","selector":"#details","required":true}],"wait":"domcontentloaded","path":"job","fields":{"title":"title","description":"description","locations":"locations","employment_type":"employment","responsibilities":"responsibilities"}}`
				html = nativeEmbeddedHTML
			}
			f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"`+scraper+`","scraper_config":`+parser+`}`, "", queue.Browser)
			if !a.RequiresRenderedDetails() {
				t.Fatal("browser ownership hid required installed renderer")
			}
			calls := 0
			renderer := heldRenderedDetail(func(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
				calls++
				if p.SourceURL != "https://example.com/job/"+f.original || p.Profile != map[string]string{"dom": "dom.rendered-detail/v1", "json-ld": "jsonld.rendered-detail/v1", "nextdata": "embedded.rendered-detail/v1"}[scraper] {
					t.Fatal("rendered canonical context differs")
				}
				return parseHeldDetail(ctx, p, heldRenderedResult(html, p.SourceURL, 200))
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered detail issued direct HTTP") })
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(context.Background(), a, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
			if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 || result.HTTP.Requests != 1 || result.HTTP.Responses != 1 {
				t.Fatal("rendered detail failed", result, err)
			}
			var title, descriptionHTML, employment, currency string
			var locations []int32
			var due time.Time
			var uploaded bool
			var canonicalHash *int64
			var pendingHash int64
			if err := f.pg.QueryRow(context.Background(), `SELECT p.titles[1],d.html,p.employment_type,p.salary_currency,p.location_ids,p.next_scrape_at,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &descriptionHTML, &employment, &currency, &locations, &due, &uploaded, &canonicalHash, &pendingHash); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || !strings.Contains(descriptionHTML, "Salary CHF") || employment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || uploaded || canonicalHash != nil || pendingHash == 0 {
				t.Fatal("rendered canonical fields/pending description differ")
			}
			score, err := f.r.ZScore(context.Background(), "scrapes_browser:example.com", f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(context.Background(), "inflight:browser").Val() != 0 || f.r.ZCard(context.Background(), "scrapes_simple:example.com").Val() != 0 {
				t.Fatal("rendered browser deadline/lease differs", err)
			}
		})
	}
}

// This fixture uses the real held-document validation/parser; it has no HTTP
// transport. The pinned mutual-TLS wire client is verified in its own package.
