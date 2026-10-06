package worker

import (
	"context"
	"fmt"
	"net/http"
	"reflect"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func personioRichXML(title, description string) string {
	return fmt.Sprintf(`<workzag-jobs><position><id>123</id><name><![CDATA[%s]]></name><office>Zurich</office><employmentType>permanent</employmentType><schedule>full-time</schedule><jobDescriptions><jobDescription><value><![CDATA[%s]]></value></jobDescription></jobDescriptions></position></workzag-jobs>`, title, description)
}

func TestRealOwnedPersonioPreservesTranslationsAndRejectsIncompleteInventory(t *testing.T) {
	for _, assignment := range []string{"skip", "json-ld", "dom"} {
		for _, mode := range []string{"translated", "promote_english", "optional404", "alternate_domain", "unavailable", "backfill_reserved"} {
			t.Run(assignment+"/"+mode, func(t *testing.T) {
				language, backfill := "en", "de"
				if mode == "promote_english" {
					language, backfill = "de", "en"
				}
				f := privateRichPipelineFixture(t, "personio", fmt.Sprintf(`{"slug":"fixture","language":"%s","backfill_languages":["%s"],"scraper_type":"%s"}`, language, backfill, assignment))
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, queue.Simple)
				if err != nil || claim == nil {
					t.Fatal("native Personio claim unavailable", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				description := "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"
				requests := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					requests++
					if mode == "unavailable" {
						if r.URL.Path == "/xml" {
							_, _ = w.Write([]byte(personioRichXML("partial", description)[:len(personioRichXML("partial", description))-15]))
							return
						}
						w.WriteHeader(404)
						return
					}
					if mode == "alternate_domain" && r.Host == "fixture.jobs.personio.de" {
						w.WriteHeader(404)
						return
					}
					if r.URL.Query().Get("language") == backfill {
						if mode == "optional404" {
							w.WriteHeader(404)
							return
						}
						if mode == "backfill_reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.Header().Set("TDM-Policy", "https://example.com/policy")
						}
					}
					title, html := "Senior Software Engineer", description
					if r.URL.Query().Get("language") == "de" {
						title, html = "Softwareentwickler &amp; Ingenieur", "<p>Deutsche Beschreibung.</p>"
					}
					_, _ = w.Write([]byte(personioRichXML(title, html)))
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatalf("native Personio did not settle: %+v %v", result, err)
				}
				assertRichDeadlineAndLease(t, f, "personio")
				var reserved bool
				var gone, failures int
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &gone, &failures); err != nil {
					t.Fatal(err)
				}
				if gone != 0 || reserved != (mode == "backfill_reserved") || failures != map[string]int{"unavailable": 1}[mode] {
					t.Fatal("Personio failure/publisher contract differs", reserved, gone, failures)
				}
				if mode == "unavailable" || mode == "backfill_reserved" {
					var count, active int
					if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 {
						t.Fatal("incomplete Personio inventory changed canonical postings", err)
					}
					return
				}
				var titles, locales []string
				var locations, technologies []int32
				var html, currency, employment, source string
				var uploaded bool
				if err := f.pg.QueryRow(ctx, `SELECT p.titles,p.locales,p.location_ids,p.technology_ids,p.salary_currency,p.employment_type,p.source_url,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.is_active`, f.board).Scan(&titles, &locales, &locations, &technologies, &currency, &employment, &source, &html, &uploaded); err != nil {
					t.Fatal(err)
				}
				wantTitles, wantLocales := []string{"Senior Software Engineer", "Softwareentwickler & Ingenieur"}, []string{"en", "de"}
				if mode == "optional404" {
					wantTitles, wantLocales = wantTitles[:1], wantLocales[:1]
				}
				domain := "de"
				if mode == "alternate_domain" {
					domain = "com"
				}
				if result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || !reflect.DeepEqual(titles, wantTitles) || !reflect.DeepEqual(locales, wantLocales) || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || currency != "CHF" || employment != "full_time" || source != "https://fixture.jobs.personio."+domain+"/job/123" || html != description || uploaded {
					t.Fatalf("Personio localized fields/primary description/lifecycle differ: titles=%v locales=%v employment=%s source=%s", titles, locales, employment, source)
				}
				wantRequests := 2
				if mode == "alternate_domain" {
					wantRequests = 3
				}
				if requests != wantRequests {
					t.Fatal("Personio request count changed", requests)
				}
			})
		}
	}
}

func TestRealOwnedPersonioEmptyXMLAndHTMLFallback(t *testing.T) {
	for _, mode := range []string{"empty_xml", "html_fallback"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "personio", `{"slug":"fixture","scraper_type":"skip"}`)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("Personio claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			requests := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				requests++
				if mode == "empty_xml" {
					_, _ = w.Write([]byte(`<workzag-jobs/>`))
					return
				}
				if r.URL.Path == "/xml" {
					w.WriteHeader(404)
					return
				}
				_, _ = w.Write([]byte(`<script>"jobs":[{"id":"123","name":"Senior Software Engineer","main_office":"Zurich","schedule":"full-time"}],"subdomain":"fixture"</script>`))
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled || result.DiscoveryError {
				t.Fatalf("authoritative Personio listing did not complete: %+v %v", result, err)
			}
			assertRichDeadlineAndLease(t, f, "personio")
			var count int
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND is_active", f.board).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if mode == "empty_xml" {
				var empty int
				if err := f.pg.QueryRow(ctx, "SELECT empty_check_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&empty); err != nil || empty != 1 {
					t.Fatal("empty-listing confirmation was not preserved", err)
				}
				if requests != 1 || count != 1 || result.Batches.Inserted != 0 || result.Cycle.Gone != 0 || result.Cycle.GoneSkipped != "" {
					t.Fatal("available empty XML was replaced by another source")
				}
				return
			}
			var titles, locales []string
			var locations []int32
			var employment string
			if err := f.pg.QueryRow(ctx, "SELECT titles,locales,location_ids,employment_type FROM job_posting WHERE board_id=$1::uuid AND is_active", f.board).Scan(&titles, &locales, &locations, &employment); err != nil {
				t.Fatal(err)
			}
			if requests != 3 || count != 1 || result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || !reflect.DeepEqual(titles, []string{"Senior Software Engineer"}) || !reflect.DeepEqual(locales, []string{"en"}) || fmt.Sprint(locations) != "[2]" || employment != "full_time" {
				t.Fatal("Personio HTML fields or fallback order differs", titles, locales, employment, requests)
			}
		})
	}
}
