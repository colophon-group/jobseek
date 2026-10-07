package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealDOMRichRowsPersistCanonicalFieldsAndPreserveFailedInventory(t *testing.T) {
	for _, route := range []string{"direct", "proxy", "rendered"} {
		for _, mode := range []string{"complete", "partial", "reserved"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				worker := queue.Simple
				md := `{"rich_rows":` + testDOMRows + `,"scraper_type":"skip","transport_attempts":1}`
				if route == "proxy" {
					md = proxyFixtureMetadata(t, md, true)
				}
				if route == "rendered" {
					md = strings.Replace(md, `{"rich_rows":`, `{"render":true,"rich_rows":`, 1)
					md = strings.Replace(md, `,"transport_attempts":1`, "", 1)
					worker = queue.Browser
				}
				f := privateRichPipelineFixture(t, "dom", md, worker)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, worker)
				if err != nil || claim == nil {
					t.Fatal("rich rows claim unavailable", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				source := "https://example.com/jobs/" + f.company
				body := fmt.Sprintf(`<article><a href="%s">Senior Software Engineer</a><div class="description"><p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p></div></article>`, source)
				if mode == "partial" {
					body += `<article><a href="/jobs/incomplete"></a></article>`
				}
				if mode == "reserved" {
					body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">` + body
				}
				requests := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					requests++
					if route == "rendered" || r.URL.Path != "/careers" {
						t.Error("rich rows left selected transport/resource")
					}
					fmt.Fprint(w, body)
				})
				if route == "proxy" {
					client = credentialedProxyFixture(t, client)
				}
				renderer := heldMonitor(func(ctx context.Context, p queue.GreenhouseMonitorProfile, c map[string]string) (RichDiscovery, error) {
					requests++
					return parseHeldRenderedMonitor(ctx, p, c, heldRenderedResult(body, p.Endpoint, 200))
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if err != nil || result == nil || !result.Settled || requests != 1 {
					t.Fatal("rich rows did not settle", result, requests, err)
				}
				assertRichDeadlineAndLease(t, f, "dom", worker)
				if mode != "complete" {
					var count, active int
					if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 {
						t.Fatal("partial/reserved rows changed canonical postings", err)
					}
					want := "failed"
					if mode == "reserved" {
						want = "publisher_reserved"
					}
					if result.Cycle.Status != want {
						t.Fatal("row rejection/policy outcome differs", result.Cycle)
					}
					return
				}
				var title, currency, html string
				var locations, technologies []int32
				if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.salary_currency,p.location_ids,p.technology_ids,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.source_url=$1`, source).Scan(&title, &currency, &locations, &technologies, &html); err != nil {
					t.Fatal(err)
				}
				if result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || title != "Senior Software Engineer" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || !strings.Contains(html, "<p>Python.") || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 0 {
					t.Fatal("rich rows lost canonical content or scheduled skip details", result, title, currency, locations, technologies, html)
				}
			})
		}
	}
}

func TestRealDOMRichRowsDelegateDetailsWithoutReplacingScrapedDescription(t *testing.T) {
	for _, mode := range []string{"new", "touched", "relisted", "future"} {
		for _, detailWorker := range []queue.WorkerType{queue.Simple, queue.Browser} {
			t.Run(mode+"/"+string(detailWorker), func(t *testing.T) {
				md := `{"rich_rows":` + testDOMRows + `,"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
				if detailWorker == queue.Browser {
					md = strings.Replace(md, `"enrich":`, `"render":true,"enrich":`, 1)
				}
				f := privateRichPipelineFixture(t, "dom", md, queue.Simple, detailWorker)
				ctx := context.Background()
				source := "https://example.com/jobs/" + f.company
				if mode != "new" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,locales=ARRAY['de'],is_active=$3,description_r2_hash=123,next_scrape_at=CASE WHEN $4 THEN now()+interval '1 day' ELSE NULL END WHERE id=$1::uuid", f.original, source, mode != "relisted", mode == "future"); err != nil {
						t.Fatal(err)
					}
					if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Scraped authoritative description</p>',123,true)", f.original); err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					fmt.Fprintf(w, `<article><a href="%s">Engineer</a><div class="description"><p>Listing teaser</p></div></article>`, source)
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("rich row delegated detail did not settle", err)
				}
				var id, title, html string
				var locales []string
				var due, future, uploaded bool
				if err := f.pg.QueryRow(ctx, "SELECT p.id::text,p.titles[1],p.locales,p.next_scrape_at IS NOT NULL,coalesce(p.next_scrape_at>now()+interval '20 hours',false),d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, source).Scan(&id, &title, &locales, &due, &future, &html, &uploaded); err != nil {
					t.Fatal(err)
				}
				if title != "Engineer" || due != (mode != "touched") {
					t.Fatal("listing title/detail deadline lost")
				}
				if mode == "new" {
					if !strings.Contains(html, "<p>Listing teaser</p>") || uploaded {
						t.Fatal("new posting lost listing fallback")
					}
				} else if html != "<p>Scraped authoritative description</p>" || !uploaded || len(locales) != 1 || locales[0] != "de" {
					t.Fatal("rich refresh replaced scraper-owned description/locales")
				}
				if mode == "future" {
					if !future || f.r.Exists(ctx, "scrape:"+id).Val() != 0 {
						t.Fatal("monitor advanced a future detail deadline")
					}
				} else if mode == "new" || mode == "relisted" {
					if f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
						t.Fatal("independent detail worker/URL lost")
					}
				}
				assertRichDeadlineAndLease(t, f, "dom")
			})
		}
	}
}

func TestRealDOMPartialRichRowsInsertAndHealMissingContentThroughIndependentDetails(t *testing.T) {
	for _, mode := range []string{"new", "stuck"} {
		for _, detailWorker := range []queue.WorkerType{queue.Simple, queue.Browser} {
			t.Run(mode+"/"+string(detailWorker), func(t *testing.T) {
				md := `{"rich_rows":{"row_selector":"article","link_selector":"a","default_locations":["Zurich"]},"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
				if detailWorker == queue.Browser {
					md = strings.Replace(md, `"enrich":`, `"render":true,"enrich":`, 1)
				}
				f := privateRichPipelineFixture(t, "dom", md, queue.Simple, detailWorker)
				ctx := context.Background()
				source := "https://example.com/jobs/" + f.company
				if mode == "stuck" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,description_r2_hash=NULL,next_scrape_at=NULL WHERE id=$1::uuid", f.original, source); err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					fmt.Fprintf(w, `<article><a href="%s">Engineer</a></article>`, source)
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("partial rich rows did not settle", result, err)
				}
				var id, title string
				var due, missingContent bool
				if err := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL,description_r2_hash IS NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &title, &due, &missingContent); err != nil {
					t.Fatal(err)
				}
				if title != "Engineer" || !due || !missingContent || f.r.ZCard(ctx, "ft_scrapes_"+string(detailWorker)+":example.com").Val() != 1 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("partial row lost title, detail owner or missing-content recovery")
				}
				assertRichDeadlineAndLease(t, f, "dom")
			})
		}
	}
}
