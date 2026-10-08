package worker

import (
	"context"
	"fmt"
	"net/http"
	"testing"
)

func TestRealExplicitSitemapVariantsPreserveRootRetryShardPolicyAndQueues(t *testing.T) {
	for _, mode := range []string{"complete", "html-retry", "html-exhausted", "foreign-child", "late-reservation", "missing-root-retry"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "sitemap", `{"sitemap_url":"https://assets.example.com/jobs.xml","xml_attempts":3,"url_filter":"/jobs/","scraper_type":"json-ld"}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			roots, children := 0, 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Host != "assets.example.com" {
					t.Error("sitemap left configured root origin", r.Host)
				}
				if r.URL.Path == "/jobs.xml" {
					roots++
					if mode == "html-exhausted" || mode == "html-retry" && roots < 3 {
						fmt.Fprint(w, `<html><body>CDN error</body></html>`)
						return
					}
					if mode == "missing-root-retry" && roots < 3 {
						w.WriteHeader(404)
						return
					}
					child := "https://assets.example.com/shard.xml"
					if mode == "foreign-child" {
						child = "https://foreign.example/shard.xml"
					}
					fmt.Fprintf(w, `<sitemapindex><sitemap><loc>%s</loc></sitemap></sitemapindex>`, child)
					return
				}
				children++
				if r.URL.Path != "/shard.xml" {
					t.Error("unbound child", r.URL)
				}
				if mode == "late-reservation" {
					w.Header().Set("TDM-Reservation", "1")
				}
				fmt.Fprint(w, `<urlset><url><loc>https://example.com/jobs/new</loc></url></urlset>`)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || !result.Settled {
				t.Fatal(result, err)
			}
			var count, missing, failures int
			var active, reserved bool
			f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count)
			f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing)
			f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved)
			switch mode {
			case "complete", "html-retry", "missing-root-retry":
				if count != 2 || result.Batches.Inserted != 1 || failures != 0 || active || children != 1 {
					t.Fatal("complete sitemap lost", result, roots, children)
				}
			default:
				if count != 1 || !active || missing != 3 {
					t.Fatal("failed sitemap wrote partial inventory", result)
				}
				if mode == "late-reservation" {
					if !reserved || failures != 0 {
						t.Fatal("shard policy lost", result)
					}
				} else if failures != 1 {
					t.Fatal("failure not recorded", result)
				}
			}
			if mode == "html-retry" || mode == "html-exhausted" || mode == "missing-root-retry" {
				if roots != 3 {
					t.Fatal("configured content budget differs", roots)
				}
			}
			if mode == "foreign-child" && children != 0 {
				t.Fatal("foreign shard fetched", children)
			}
			assertRichDeadlineAndLease(t, f, "sitemap")
		})
	}
}
