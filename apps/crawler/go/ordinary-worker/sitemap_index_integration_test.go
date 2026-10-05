package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealOwnedSitemapNestedIndexUnionAndChildPublisherPolicy(t *testing.T) {
	for _, mode := range []string{"success", "later_failure", "header", "meta", "empty_leaf", "missing", "foreign"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "sitemap", `{"sitemap_url":"https://example.com/jobs.xml","url_filter":"/jobs/","scraper_type":"json-ld"}`)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			calls := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls[r.URL.Path]++
				if r.Host != "example.com" || r.Header.Get("User-Agent") != "jobseek-crawler (+https://jseek.co/)" {
					t.Error("index escaped sealed sitemap transport")
				}
				switch r.URL.Path {
				case "/jobs.xml":
					fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://example.com/jobs-nested.xml</loc></sitemap></sitemapindex>`)
				case "/jobs-nested.xml":
					if mode == "foreign" {
						fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://foreign.example/jobs.xml</loc></sitemap></sitemapindex>`)
						return
					}
					fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://example.com/jobs.xml</loc></sitemap><sitemap><loc>https://example.com/jobs-a.xml</loc></sitemap><sitemap><loc>https://example.com/jobs-b.xml</loc></sitemap></sitemapindex>`)
				case "/jobs-a.xml", "/jobs-b.xml":
					if mode == "missing" {
						w.WriteHeader(404)
						return
					}
					if mode == "empty_leaf" {
						fmt.Fprint(w, `<urlset/>`)
						return
					}
					if r.URL.Path == "/jobs-b.xml" {
						switch mode {
						case "later_failure":
							w.WriteHeader(503)
							return
						case "header":
							w.Header().Set("TDM-Reservation", "1")
							w.Header().Set("TDM-Policy", "child-policy")
							w.WriteHeader(404)
							return
						case "meta":
							fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="child-policy">`)
							return
						}
					}
					fmt.Fprintf(w, `<urlset><url><loc>https://example.com/jobs/%s?utm_source=fixture</loc></url></urlset>`, strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/jobs-"), ".xml"))
				default:
					t.Error("unexpected shard", r.URL.Path)
				}
			}))
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatal(result, err)
			}
			var active, reserved bool
			var missing, failures, count int
			var evidence *string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence, &count); err != nil {
				t.Fatal(err)
			}
			if mode == "success" {
				if result.Batches.Inserted != 2 || count != 3 || active || missing != 4 || failures != 0 || reserved || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 2 {
					t.Fatal("complete nested union changed", result)
				}
			} else if mode == "header" || mode == "meta" {
				if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || !active || missing != 3 || count != 1 || evidence == nil || !strings.Contains(*evidence, "https://example.com/jobs-b.xml") || !strings.Contains(*evidence, "child-policy") {
					t.Fatal("child reservation lost actual resource or changed inventory", result.Cycle, evidence)
				}
			} else {
				// Existing empty-inventory confirmation protects the old posting.
				if !active || missing != 3 || count != 1 || reserved || mode != "empty_leaf" && failures != 1 {
					t.Fatal("incomplete index changed prior inventory", result.Cycle)
				}
			}
			want := 1
			if mode == "later_failure" {
				want = 3
			}
			if calls["/jobs.xml"] != 1 || calls["/jobs-nested.xml"] != 1 || mode != "foreign" && calls["/jobs-b.xml"] != want {
				t.Fatal("cycle or child retry budget changed", calls)
			}
			assertRichDeadlineAndLease(t, f, "sitemap")
		})
	}
}
