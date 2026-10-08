package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealSuccessFactorsDetailFieldsPreserveMetadataFilteringAndWholeInventory(t *testing.T) {
	for _, mode := range []string{"company", "required", "optional-missing", "required-missing", "marker-retry", "marker-exhausted", "redirect", "foreign-redirect", "loop", "notfound", "late-reservation", "url-prefilter"} {
		t.Run(mode, func(t *testing.T) {
			fields := `"fetch_company":true,"job_filter":{"field":"metadata.company","exclude":"^QS Security$"}`
			if strings.HasPrefix(mode, "required") {
				fields = `"detail_fields":{"service":"dept","adcode":"adcode"},"job_filter":{"field":"metadata.service","include":"^Roads$"}`
			}
			if mode == "url-prefilter" {
				fields += `,"url_filter":"/us/job/"`
			}
			metadata := `{"preset":"successfactors","feed_url":"https://example.com/googlefeed.xml","scraper_type":"skip",` + fields + `}`
			f := privateRichPipelineFixture(t, "rss", metadata)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			feedCalls, detailCalls := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/googlefeed.xml" {
					feedCalls++
					urlB := "https://example.com/eu/job/2"
					if mode == "url-prefilter" {
						urlB = "https://foreign.example/eu/job/2"
					}
					fmt.Fprintf(w, `<rss xmlns:g="http://base.google.com/ns/1.0"><channel><item><link>https://example.com/us/job/1</link><title>Software Engineer</title><description><![CDATA[<p>Python. Salary CHF 100000-120000 yearly.</p>]]></description><g:location>Zurich, Switzerland</g:location></item><item><link>%s</link><title>Security Engineer</title><description><![CDATA[<p>Security.</p>]]></description><g:location>Zurich, Switzerland</g:location></item></channel></rss>`, urlB)
					return
				}
				detailCalls++
				if r.Host != "example.com" {
					t.Error("detail left authenticated feed origin", r.Host)
				}
				switch mode {
				case "foreign-redirect":
					http.Redirect(w, r, "https://foreign.example/steal", 302)
					return
				case "loop":
					http.Redirect(w, r, r.URL.String(), 302)
					return
				case "notfound":
					w.WriteHeader(404)
					return
				case "late-reservation":
					w.Header().Set("TDM-Reservation", "1")
				case "redirect":
					if !strings.HasPrefix(r.URL.Path, "/resolved/") {
						http.Redirect(w, r, "/resolved"+r.URL.Path, 302)
						return
					}
				case "marker-exhausted":
					fmt.Fprint(w, `<p>Origin error</p>`)
					return
				case "marker-retry":
					if detailCalls == 1 {
						fmt.Fprint(w, `<p>Origin error</p>`)
						return
					}
				}
				company, dept := "Main", "Roads"
				if strings.HasSuffix(r.URL.Path, "/eu/job/2") {
					company, dept = "QS Security", "Other"
				}
				fmt.Fprint(w, `<h1 data-careersite-propertyid="title">Engineer</h1>`)
				if mode != "optional-missing" {
					fmt.Fprintf(w, `<div data-careersite-propertyid="customfield1">%s</div>`, company)
				}
				if mode != "required-missing" {
					fmt.Fprintf(w, `<span data-careersite-propertyid="dept">%s</span><span data-careersite-propertyid="adcode"><b>AB</b>123</span>`, dept)
				}
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || !result.Settled || feedCalls != 1 {
				t.Fatal(result, err, feedCalls)
			}
			var count, missing, failures int
			var active, reserved bool
			if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "company", "required", "marker-retry", "redirect", "url-prefilter":
				if count != 2 || result.Batches.Inserted != 1 || failures != 0 || active {
					t.Fatal("detail metadata/filter lost", result, count, failures)
				}
			case "optional-missing":
				if count != 3 || result.Batches.Inserted != 2 || failures != 0 {
					t.Fatal("optional historic company became required", result)
				}
			default:
				if count != 1 || !active || missing != 3 {
					t.Fatal("failed detail field request published inventory", result, count, missing)
				}
				if mode == "late-reservation" {
					if !reserved || failures != 0 {
						t.Fatal("detail publisher reservation lost", result)
					}
				} else if failures != 1 {
					t.Fatal("failed detail field cycle not recorded", result)
				}
			}
			if mode == "url-prefilter" && detailCalls != 1 {
				t.Fatal("URL prefilter fetched foreign region", detailCalls)
			}
			if mode == "foreign-redirect" && detailCalls != 1 {
				t.Fatal("foreign redirect issued unbound request", detailCalls)
			}
			if mode == "marker-exhausted" && detailCalls != 3 {
				t.Fatal("invalid HTML classification budget lost", detailCalls)
			}
			assertRichDeadlineAndLease(t, f, "rss")
		})
	}
}
