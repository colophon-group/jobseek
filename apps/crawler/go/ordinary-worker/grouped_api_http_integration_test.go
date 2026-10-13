package worker

import (
	"context"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"testing"
)

func TestRealGroupedAPIHTTPReplacementCanonicalSalaryAndPolicy(t *testing.T) {
	for _, slug := range []string{"sage", "mckinsey"} {
		for _, mode := range []string{"complete", "later-reserved", "later-failed"} {
			t.Run(slug+"/"+mode, func(t *testing.T) {
				board, endpoint := "https://www.sage.com/en-gb/company/careers/career-search/", "https://www.sage.com/api/sagedotcom/CareerSearch/GetCareerSearchData/"
				if slug == "mckinsey" {
					board, endpoint = "https://www.mckinsey.com/careers/search-jobs", "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
				}
				metadata := fmt.Sprintf(`{"api_url":%q,"json_path":"jobs","url_field":"url","fields":{"title":"title","description":"description","locations":"location","base_salary":["=Salary range: ","salary"]},"transport_attempts":1,"pagination":{"param_name":"page","start_value":1,"increment":1},"scraper_type":"skip"}`, endpoint)
				f := privateRichPipelineFixtureURL(t, "api_sniffer", metadata, board, queue.Simple)
				ctx := context.Background()
				claim, e := f.a.Claim(ctx, queue.Simple)
				if e != nil || claim == nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					if calls == 2 && mode == "later-reserved" {
						w.Header().Set("Tdm-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if calls >= 2 && mode == "later-failed" {
						w.WriteHeader(503)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprintf(w, `{"total":2,"jobs":[{"url":"https://%s/jobs/%d","title":"Senior Software Engineer","description":"<p>Salary CHF 100000-120000 yearly. Build Go services.</p>","location":"Zurich","salary":"$192,000 - $192,000"}]}`, r.Host, calls)
				})
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("native HTTP terminal settlement", e)
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", queue.Simple)
				var inserted, missing, failures int
				var reserved bool
				if e := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted); e != nil {
					t.Fatal(e)
				}
				if mode == "complete" {
					if inserted != 2 || missing != 1 || failures != 0 || reserved {
						t.Fatal("complete inventory conservation", inserted, missing, failures)
					}
					var valid int
					if e := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid AND p.salary_min=100000 AND p.salary_max=120000 AND p.salary_currency='CHF' AND d.html LIKE '%Salary CHF%' AND d.r2_uploaded=false AND p.description_r2_hash IS NULL`, f.board, f.original).Scan(&valid); e != nil || valid != 2 {
						t.Fatal("original description-derived canonical salary/staging changed", e, valid)
					}
				} else {
					want := 1
					if mode == "later-reserved" {
						want = 0
					}
					if inserted != 0 || missing != 0 || failures != want || reserved != (mode == "later-reserved") {
						t.Fatal("late failure/reservation wrote prefix or absence", inserted, missing, failures, reserved)
					}
				}
			})
		}
	}
}
