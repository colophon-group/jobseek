package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const yumFixtureMetadata = `{"source":"browser","browser_expression":"({jobs: jobList})","path":"jobs","url_template":"{link}","fields":{"title":"title","description":"desc","locations":"=Shanghai, China","employment_type":"=full_time","job_location_type":"=onsite"},"wait":"domcontentloaded","timeout":30000,"scraper_type":"skip"}`

func TestRealAmazonAndYumStreamWritesFailurePrefixesAndPublisherPolicy(t *testing.T) {
	for _, provider := range []string{"amazon", "nextdata"} {
		for _, mode := range []string{"complete", "first-reserved", "later-reserved", "later-failure", "invalid-inventory"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				metadata, board, worker := `{"scraper_type":"skip"}`, "https://www.amazon.jobs/en/search", queue.Simple
				if provider == "nextdata" {
					metadata, board, worker = yumFixtureMetadata, api.YumChinaBoardURL, queue.Browser
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, board, worker)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_board SET empty_check_count=3 WHERE id=$1::uuid", f.board); err != nil {
					t.Fatal(err)
				}
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				claim, err := f.a.Claim(ctx, worker)
				if err != nil || claim == nil {
					t.Fatal("installed provider claim missing", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					later := r.URL.Query().Get("offset") == "100" || r.URL.Path == "/yumchina/js/job.js"
					if mode == "first-reserved" && !later || mode == "later-reserved" && later {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						fmt.Fprint(w, "reserved")
						return
					}
					if mode == "later-failure" && later {
						w.WriteHeader(503)
						return
					}
					if provider == "nextdata" {
						if !later {
							fmt.Fprint(w, `<script src="js/job.js"></script>`)
							return
						}
						if mode == "invalid-inventory" {
							fmt.Fprint(w, `let jobList = [{bad:unknown()}];`)
							return
						}
						fmt.Fprintf(w, `let jobList = [{type:"Track",title:"Software Engineer",desc:"<p>We are looking for a software engineer to build and maintain our reliable platform with our team.</p>",link:"https://xyz.51job.com/external/apply.aspx?jobid=%s&ctmid=6366958"}];`, f.company)
						return
					}
					if r.URL.Path != "/en/search.json" || r.URL.Query().Get("result_limit") != "100" || r.URL.Query().Get("sort") != "recent" {
						t.Error("Amazon request left exact API query")
					}
					if mode == "invalid-inventory" {
						fmt.Fprint(w, `{"hits":1,"jobs":[false]}`)
						return
					}
					total := 1
					if strings.HasPrefix(mode, "later-") {
						total = 101
					}
					fmt.Fprintf(w, `{"hits":%d,"jobs":[{"job_path":"/en/jobs/%s/software-engineer","title":"Software Engineer","description":"<p>We are looking for a software engineer to build and maintain our reliable platform with our team.</p>","country_code":"CHE","normalized_location":"Zurich, Switzerland","salary":"$40/hr ... $50/hr","posted_date":"March  9, 2026"}]}`, total, f.company)
				})
				renderer := &NativeRenderedDetails{client: &lp.Client{}, slots: make(chan struct{}, 1)}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("owned claim did not settle", err)
				}
				assertRichDeadlineAndLease(t, f, provider, worker)
				var reserved, active bool
				var failures, empties, postings, missing int
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,empty_check_count,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &empties, &postings); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
					t.Fatal(err)
				}
				if mode == "complete" {
					if reserved || failures != 0 || empties != 0 || result.Batches.Inserted != 1 || postings != 2 {
						t.Fatal("complete rich inventory not persisted", result.Batches, postings, failures, empties)
					}
					return
				}
				prefix := provider == "amazon" && strings.HasPrefix(mode, "later-")
				wantPosts := 1
				if prefix {
					wantPosts = 2
				}
				wantFailures := 1
				if strings.HasSuffix(mode, "reserved") {
					wantFailures = 0
				}
				if reserved != strings.HasSuffix(mode, "reserved") || failures != wantFailures || empties != 3 || postings != wantPosts || !active || missing != 3 {
					t.Fatal("partial, invalid or reserved inventory changed absence state", reserved, failures, empties, postings, active, missing)
				}
				if prefix && result.Batches.Inserted != 1 {
					t.Fatal("verified streamed prefix was lost")
				}
				if calls < 1 || calls > 2 {
					t.Fatal("unexpected bounded request count", calls)
				}
			})
		}
	}
}
