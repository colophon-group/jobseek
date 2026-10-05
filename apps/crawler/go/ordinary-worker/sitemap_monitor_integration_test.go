package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealOwnedSitemapCompleteInventoryFiltersFailureAndPolicy(t *testing.T) {
	for _, mode := range []string{"success", "retry429", "empty", "status404", "malformed", "index", "header", "meta"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "sitemap", `{"sitemap_url":"https://example.com/jobs.xml","url_filter":{"include":"/jobs/\\w+","exclude":"intern"},"scraper_type":"json-ld"}`)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.Host != "example.com" || r.URL.Path != "/jobs.xml" && !(mode == "index" && r.URL.Path == "/child.xml") {
					t.Error("sitemap left exact direct resource")
				}
				if mode == "retry429" && calls.Load() < 3 {
					w.WriteHeader(429)
					return
				}
				switch mode {
				case "empty":
					w.WriteHeader(200)
				case "status404":
					w.WriteHeader(404)
				case "malformed":
					fmt.Fprint(w, `<urlset>`)
				case "index":
					fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://example.com/child.xml</loc></sitemap></sitemapindex>`)
				case "header":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "sitemap-policy")
					w.WriteHeader(404)
				case "meta":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="sitemap-policy">`)
				default:
					fmt.Fprint(w, `<urlset><url><loc>https://example.com/jobs/工程師?utm_source=fixture</loc></url><url><loc>https://example.com/jobs/工程師</loc></url><url><loc>https://example.com/jobs/intern</loc></url><url><loc>https://example.com/contact</loc></url></urlset>`)
				}
			}))
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatal(result, err)
			}
			var active, reserved bool
			var missing, failures, postings int
			var evidence *string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence, &postings); err != nil {
				t.Fatal(err)
			}
			if mode == "success" || mode == "retry429" {
				want := int64(1)
				if mode == "retry429" {
					want = 3
				}
				if result.Batches.Inserted != 1 || active || missing != 4 || postings != 2 || reserved || failures != 0 || calls.Load() != want {
					t.Fatal("canonical inventory/filter/absence changed", result)
				}
				if n := f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val(); n != 1 {
					t.Fatal("separate detail not queued", n)
				}
			} else {
				if !active || missing != 3 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("failed/reserved inventory changed postings")
				}
				if mode == "header" || mode == "meta" {
					if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || calls.Load() != 1 || evidence == nil || !strings.Contains(*evidence, "https://example.com/jobs.xml") || !strings.Contains(*evidence, "sitemap-policy") {
						t.Fatal("policy/resource lost", result.Cycle, evidence)
					}
				} else {
					want := int64(1)
					if mode == "index" {
						want = 2
					}
					if mode == "empty" {
						want = 3
					}
					if result.Cycle.Status != "failed" || failures != 1 || reserved || calls.Load() != want {
						t.Fatal("failure became inventory/provider disappearance", result.Cycle, calls.Load())
					}
				}
			}
			assertRichDeadlineAndLease(t, f, "sitemap")
		})
	}
}
