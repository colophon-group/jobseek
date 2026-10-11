package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func enrichmentFixtureMetadata(t *testing.T, scraper string, fields []string) string {
	t.Helper()
	options := map[string]any{"enrich": fields}
	if scraper == "dom" {
		options["steps"] = []any{
			map[string]any{"tag": "h1", "field": "title"},
			map[string]any{"tag": "p", "attr": "data-field=location", "field": "location"},
			map[string]any{"tag": "h2", "text": "Role", "offset": 1, "field": "description", "html": true},
		}
	}
	body, err := json.Marshal(map[string]any{"scraper_type": scraper, "scraper_config": options})
	if err != nil {
		t.Fatal(err)
	}
	return string(body)
}

func TestRealSharedDOMJSONLDSelectedWriterFieldsPreserveCanonicalData(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		for _, field := range []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary"} {
			t.Run(scraper+"/"+field, func(t *testing.T) {
				f, authority, claim := independentDetailOwnedFixture(t, enrichmentFixtureMetadata(t, scraper, []string{field}), "")
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET titles=ARRAY['Monitor title'],locales=ARRAY['sv'],location_ids=ARRAY[1],location_types=ARRAY['remote'],employment_type='part_time',salary_min=1234,salary_max=2345,salary_currency='EUR',salary_period='year' WHERE id=$1::uuid`, f.original); err != nil {
					t.Fatal(err)
				}
				detail, err := authority.ReadWorkdayDetail(ctx, claim)
				if err != nil {
					t.Fatal(err)
				}
				values := map[string]any{"title": "Senior Software Engineer", "description": "<p>Python. Salary CHF 100000-120000 yearly.</p>", "locations": []any{"Zurich"}, "employment_type": "FULL_TIME", "job_location_type": "remote", "date_posted": "2026-10-01", "base_salary": map[string]any{"currency": "CHF", "value": 100000}}
				receipt, err := PersistDetailContent(ctx, authority, detail, richPipelinePreparer(t, f).Processor, values)
				if err != nil || receipt == nil {
					t.Fatal("selected canonical write failed", err)
				}
				if err := authority.Settle(ctx, claim, receipt); err != nil {
					t.Fatal(err)
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
				score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result()
				if err != nil || lease != nil || !due.After(time.Now()) || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("selected write lost schedule or lease conservation", err)
				}
			})
		}
	}
}

func TestRealSharedDOMJSONLDEnrichmentFetchFailurePolicyAndBackfill(t *testing.T) {
	for _, scraper := range []string{"dom", "json-ld"} {
		for _, mode := range []string{"description", "locations-only", "backfill", "selected-empty", "selected-empty-third", "reserved", "transient", "transient-third"} {
			t.Run(scraper+"/"+mode, func(t *testing.T) {
				fields := []string{"description"}
				if mode == "locations-only" {
					fields = []string{"locations"}
				}
				f, authority, claim := independentDetailOwnedFixture(t, enrichmentFixtureMetadata(t, scraper, fields), "")
				ctx := context.Background()
				if mode != "backfill" {
					if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET titles=ARRAY['Monitor title'],location_ids=ARRAY[1],employment_type='part_time' WHERE id=$1::uuid`, f.original); err != nil {
						t.Fatal(err)
					}
				} else {
					if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET titles='{}',location_ids='{}',employment_type=NULL WHERE id=$1::uuid`, f.original); err != nil {
						t.Fatal(err)
					}
				}
				if strings.HasSuffix(mode, "-third") {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET scrape_failures=2 WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if strings.HasPrefix(mode, "transient") {
						w.WriteHeader(503)
						return
					}
					body := nativeJSONLDHTML
					if scraper == "dom" {
						body = nativeDOMHTML
					}
					if strings.HasPrefix(mode, "selected-empty") {
						body = `<html><h1>Existing title</h1></html>`
						if scraper == "json-ld" {
							body = `<html><script type="application/ld+json">{"@type":"JobPosting","title":"Existing title"}</script></html>`
						}
					}
					fmt.Fprint(w, body)
				})
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, authority, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				if err != nil || !result.Settled || calls == 0 {
					t.Fatal("masked fetch did not settle", err)
				}
				var title string
				var active, reserved bool
				var descriptions, failures int
				var locations []int32
				if err := f.pg.QueryRow(ctx, `SELECT coalesce(titles[1],''),is_active,tdm_reserved,scrape_failures,location_ids,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &failures, &locations, &descriptions); err != nil {
					t.Fatal(err)
				}
				wantFailures := 0
				if strings.HasPrefix(mode, "selected-empty") || strings.HasPrefix(mode, "transient") {
					wantFailures = 1
					if strings.HasSuffix(mode, "-third") {
						wantFailures = 3
					}
				}
				if !active || failures != wantFailures || reserved != (mode == "reserved") {
					t.Fatal("masked transient counter/active policy differs")
				}
				if mode == "backfill" {
					if title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || descriptions != 1 {
						t.Fatal("URL-only core backfill lost")
					}
				} else if title != "Monitor title" {
					t.Fatal("enrichment overwrote monitor title")
				}
				if mode == "locations-only" && (fmt.Sprint(locations) != "[2]" || descriptions != 0) {
					t.Fatal("location-only mask leaked description")
				}
				if mode == "description" && descriptions != 1 {
					t.Fatal("selected description not staged")
				}
				if strings.HasPrefix(mode, "selected-empty") || mode == "reserved" || strings.HasPrefix(mode, "transient") {
					if descriptions != 0 {
						t.Fatal("uncertain/policy response wrote description")
					}
				} else if descriptions > 0 {
					var html string
					if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&html); err != nil || !strings.Contains(html, "Salary CHF") {
						t.Fatal("canonical description text changed", err)
					}
				}
				var due *time.Time
				var lease *time.Time
				if err := f.pg.QueryRow(ctx, "SELECT next_scrape_at,leased_until FROM job_posting WHERE id=$1::uuid", f.original).Scan(&due, &lease); err != nil {
					t.Fatal(err)
				}
				if lease != nil || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("masked settlement retained a lease")
				}
				if strings.HasSuffix(mode, "-third") {
					if due != nil || f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Err() == nil {
						t.Fatal("original third-transient parking differs")
					}
				} else if due == nil || !due.After(time.Now()) || f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Val() != float64(due.UnixMicro())/1e6 {
					t.Fatal("masked schedule not conserved")
				}
			})
		}
	}
}
