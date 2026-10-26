package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealRenderedSelectedWriterFieldsPreserveCanonicalData(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld", "nextdata", "api_sniffer"} {
		fields := []string{"title", "description", "locations", "employment_type"}
		if scraper == "nextdata" {
			fields = []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary", "metadata"}
		}
		for _, field := range fields {
			t.Run(scraper+"/"+field, func(t *testing.T) {
				md := enrichmentFixtureMetadata(t, scraper, []string{field})
				var config map[string]any
				if json.Unmarshal([]byte(md), &config) != nil {
					t.Fatal("fixture config")
				}
				options := config["scraper_config"].(map[string]any)
				options["render"] = true
				if scraper == "api_sniffer" {
					delete(options, "render")
					options["fields"] = map[string]any{"title": "title", "description": "description", "locations": "locations", "employment_type": "employment"}
				}
				if scraper == "dom" {
					options["defaults"] = map[string]any{"employment_type": "FULL_TIME", "language": "en"}
				}
				if scraper == "nextdata" {
					options["path"] = "job"
					options["fields"] = map[string]any{"title": "title", "description": "description", "locations": "locations", "employment_type": "employment", "responsibilities": "responsibilities", "job_location_type": "mode", "date_posted": "posted", "base_salary": "salary", "metadata.team": "team"}
				}
				body, _ := json.Marshal(config)
				f, authority, claim := independentDetailOwnedFixture(t, string(body), "", queue.Browser)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET titles=ARRAY['Monitor title'],locales=ARRAY['sv'],location_ids=ARRAY[1],location_types=ARRAY['remote'],employment_type='part_time',salary_min=1234,salary_max=2345,salary_currency='EUR',salary_period='year' WHERE id=$1::uuid`, f.original); err != nil {
					t.Fatal(err)
				}
				calls := 0
				renderer := heldRenderedDetail(func(ctx context.Context, p queue.WorkdayDetailProfile) (map[string]any, *policy.Reservation, error) {
					calls++
					if len(p.EnrichmentFields) != 1 || p.EnrichmentFields[0] != field {
						t.Fatal("selected field lost before rendering")
					}
					html := nativeJSONLDHTML
					if scraper == "dom" {
						html = nativeDOMHTML
					}
					if scraper == "nextdata" {
						html = strings.Replace(nativeEmbeddedHTML, `{"job":{`, `{"job":{"mode":"onsite","posted":"2026-10-01","salary":{"currency":"CHF","value":100000},"team":"Research",`, 1)
					}
					if scraper == "api_sniffer" {
						return map[string]any{"title": "Senior Software Engineer", "description": "<p>Salary CHF 100000 per year.</p>", "locations": []any{"Zurich"}, "employment_type": "FULL_TIME"}, nil, nil
					}
					return parseHeldDetail(ctx, p, heldRenderedResult(html, p.SourceURL, 200))
				})
				client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Error("rendered selection issued direct HTTP") })
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, authority, claim, client, richPipelinePreparer(t, f).Processor, circuits, renderer)
				if err != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 {
					t.Fatal("held selected write failed", err, result, calls)
				}
				var title, employment string
				var locations []int32
				var salary int64
				var descriptions int
				var due time.Time
				var lease *time.Time
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],employment_type,location_ids,salary_min,(SELECT count(*) FROM descriptions WHERE posting_id=p.id),next_scrape_at,leased_until FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &employment, &locations, &salary, &descriptions, &due, &lease); err != nil {
					t.Fatal(err)
				}
				wantTitle, wantEmployment, wantLocations, wantSalary, wantDescriptions := "Monitor title", "part_time", "[1]", int64(1234), 0
				if field == "title" {
					wantTitle = "Senior Software Engineer"
				}
				if field == "employment_type" {
					wantEmployment = "full_time"
				}
				if field == "locations" {
					wantLocations = "[2]"
				}
				if field == "description" {
					wantSalary, wantDescriptions = 100000, 1
				}
				// The original enrichment path stores dates/structured salary only
				// in its retired compatibility payload, and resolves location type
				// with a selected/backfilled locations field. These masks must not
				// invent additional canonical column writes.
				if title != wantTitle || employment != wantEmployment || fmt.Sprint(locations) != wantLocations || salary != wantSalary || descriptions != wantDescriptions {
					t.Fatalf("selected/coalesced fields changed: title=%q employment=%q locations=%v salary=%d descriptions=%d", title, employment, locations, salary, descriptions)
				}
				score, err := f.r.ZScore(ctx, "scrapes_browser:example.com", f.original).Result()
				if err != nil || lease != nil || !due.After(time.Now()) || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:browser").Val() != 0 {
					t.Fatal("selected write lost schedule or lease conservation", err)
				}
			})
		}
	}
}
