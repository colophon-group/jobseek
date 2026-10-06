package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealOwnedPhenomLocalesShardsPublisherPolicyAndQueueConservation(t *testing.T) {
	realPhenomTransportCases(t, false)
}
func TestRealProxyPhenomLocalesShardsPublisherPolicyAndQueueConservation(t *testing.T) {
	realPhenomTransportCases(t, true)
}
func realPhenomTransportCases(t *testing.T, proxy bool) {
	for _, mode := range []string{"success", "later_failure", "retry403", "retry202", "root_missing", "root_invalid", "root_nonxml", "child_missing", "child_invalid", "empty_leaf", "root_header", "header", "meta", "foreign"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "phenom", proxyFixtureMetadata(t, `{"sitemap_url":"https://example.com/sitemap.xml","scraper_type":"json-ld"}`, proxy))
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			calls := map[string]int{}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls[r.URL.Path]++
				if r.Host != "example.com" || r.Method != "GET" || r.Header.Get("User-Agent") != "jobseek-crawler (+https://jseek.co/)" {
					t.Error("Phenom request escaped bound transport")
				}
				w.Header().Set("Content-Type", "text/xml")
				switch r.URL.Path {
				case "/sitemap.xml":
					if mode == "root_missing" {
						w.WriteHeader(404)
						return
					}
					if mode == "root_invalid" {
						fmt.Fprint(w, "<bad")
						return
					}
					if mode == "root_nonxml" {
						w.Header().Set("Content-Type", "text/html")
					}
					if mode == "root_header" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "root-policy")
					}
					fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://example.com/sitemap-aa-en.xml</loc></sitemap><sitemap><loc>https://example.com/sitemap-bb-en.xml</loc></sitemap><sitemap><loc>https://example.com/sitemap-cc-de.xml</loc></sitemap></sitemapindex>`)
				case "/sitemap-aa-en.xml", "/sitemap-bb-en.xml":
					if mode == "child_missing" {
						w.WriteHeader(410)
						return
					}
					if mode == "child_invalid" {
						fmt.Fprint(w, "<bad")
						return
					}
					if mode == "empty_leaf" {
						fmt.Fprint(w, "<urlset/>")
						return
					}
					if r.URL.Path == "/sitemap-bb-en.xml" {
						switch mode {
						case "later_failure":
							w.WriteHeader(503)
							return
						case "retry403":
							w.WriteHeader(403)
							return
						case "retry202":
							w.WriteHeader(202)
							return
						case "header":
							w.Header().Set("TDM-Reservation", "1")
							w.Header().Set("TDM-Policy", "child-policy")
							fmt.Fprint(w, "<bad")
							return
						case "meta":
							fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="child-policy">`)
							return
						case "foreign":
							fmt.Fprint(w, `<sitemapindex><sitemap><loc>https://foreign.example/child.xml</loc></sitemap></sitemapindex>`)
							return
						}
					}
					fmt.Fprintf(w, `<urlset><url><loc>https://example.com/job/%s?utm_source=fixture</loc></url></urlset>`, strings.TrimSuffix(strings.TrimPrefix(r.URL.Path, "/sitemap-"), ".xml"))
				default:
					t.Error("unexpected or filtered locale request", r.URL.Path)
				}
			}))
			preparer := &pipelinePreparer{}
			if proxy {
				client = credentialedProxyFixture(t, client)
			}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatal(result, err)
			}
			var active, reserved bool
			var missing, failures, count, gone int
			var evidence *string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text,gone_confirmation_count,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence, &gone, &count); err != nil {
				t.Fatal(err)
			}
			if mode == "success" {
				if result.Batches.Inserted != 2 || count != 3 || active || missing != 4 || failures != 0 || reserved || gone != 0 || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 2 {
					t.Fatal("Phenom complete selected union changed", result)
				}
			} else if mode == "root_header" || mode == "header" || mode == "meta" {
				resource, policy := "https://example.com/sitemap-bb-en.xml", "child-policy"
				if mode == "root_header" {
					resource, policy = "https://example.com/sitemap.xml", "root-policy"
				}
				if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || gone != 0 || !active || missing != 3 || count != 1 || evidence == nil || !strings.Contains(*evidence, resource) || !strings.Contains(*evidence, policy) {
					t.Fatal("Phenom publisher provenance or inventory changed", result.Cycle, evidence)
				}
			} else {
				wantFailure := 0
				if mode == "later_failure" || mode == "retry403" || mode == "retry202" || mode == "foreign" {
					wantFailure = 1
				}
				if !active || missing != 3 || count != 1 || reserved || gone != 0 || failures != wantFailure {
					t.Fatal("Phenom incomplete inventory changed canonical state", result.Cycle, failures, wantFailure)
				}
			}
			if calls["/sitemap.xml"] != 1 || calls["/sitemap-cc-de.xml"] != 0 {
				t.Fatal("root attempt or language filter differs", calls)
			}
			if mode == "later_failure" || mode == "retry403" || mode == "retry202" {
				if calls["/sitemap-bb-en.xml"] != 3 {
					t.Fatal("child retry budget differs", calls)
				}
			}
			assertRichDeadlineAndLease(t, f, "phenom")
		})
	}
}
