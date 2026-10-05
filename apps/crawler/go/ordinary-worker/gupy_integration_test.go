package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealOwnedGupyIdentityIncompleteInventoryPolicyAndQueueConservation(t *testing.T) {
	for _, mode := range []string{"success", "retry202", "empty-listing", "incomplete", "empty-body", "gone404", "gone410", "retry403", "non200-policy", "redirect", "wrong-tenant", "challenge", "header", "meta"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "gupy", `{"tenant":"fixture","scraper_type":"json-ld"}`)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.Host != "fixture.gupy.io" || r.URL.Path != "/" {
					t.Error("listing request left bound endpoint")
				}
				if mode == "retry202" && calls.Load() < 3 {
					w.WriteHeader(202)
					return
				}
				switch mode {
				case "empty-body":
					w.WriteHeader(200)
				case "gone404":
					w.WriteHeader(404)
				case "gone410":
					w.WriteHeader(410)
				case "retry403":
					w.WriteHeader(403)
				case "non200-policy":
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(403)
				case "redirect":
					http.Redirect(w, r, "/unreviewed", 302)
				case "wrong-tenant":
					fmt.Fprint(w, `<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"other","careerPage":{},"jobs":[]}}}</script>`)
				case "challenge":
					fmt.Fprint(w, `<div id="job_listings_wrapper"><title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>`)
				case "header":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://example.com/policy")
					fmt.Fprint(w, "malformed")
				case "meta":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">`)
				case "incomplete":
					fmt.Fprint(w, `<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"fixture","careerPage":{},"jobs":[{"id":90071992547409931234},{"id":90071992547409931234},{"id":false}]}}}</script>`)
				case "empty-listing":
					fmt.Fprint(w, `<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"fixture","careerPage":{},"jobs":[]}}}</script>`)
				default:
					fmt.Fprint(w, `<script id="__NEXT_DATA__">{"props":{"pageProps":{"subdomain":"fixture","careerPage":{},"jobs":[{"id":90071992547409931234}]}}}</script>`)
				}
			}))
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatal("URL-only claim did not settle", result, err)
			}
			var active, reserved bool
			var missing, failures, postings, gone int
			var evidence *string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone, &evidence, &postings); err != nil {
				t.Fatal(err)
			}
			wantCalls := int64(1)
			if mode == "retry202" || mode == "empty-body" || mode == "retry403" || mode == "non200-policy" {
				wantCalls = 3
			}
			if calls.Load() != wantCalls {
				t.Fatal("attempt or redirect budget differs", calls.Load())
			}
			if mode == "success" || mode == "retry202" {
				if active || missing != 4 || postings != 2 || failures != 0 || reserved || result.Batches.Inserted != 1 {
					t.Fatal("canonical inventory/absence differs", result)
				}
				var source string
				if err := f.pg.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&source); err != nil || source != "https://fixture.gupy.io/jobs/90071992547409931234" {
					t.Fatal("source canonicalization differs")
				}
				if n := f.r.ZCard(ctx, "ft_scrapes_simple:fixture.gupy.io").Val(); n != 1 {
					t.Fatal("separate detail not scheduled", n)
				}
			} else if mode == "incomplete" {
				if !active || missing != 3 || postings != 2 || failures != 0 || reserved || result.Batches.Inserted != 1 {
					t.Fatal("incomplete inventory failed to retain known jobs and accepted precise IDs", result)
				}
			} else {
				if !active || missing != 3 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("failed/reserved/empty inventory changed canonical jobs")
				}
				if mode == "header" || mode == "meta" {
					if !reserved || failures != 0 || evidence == nil || !strings.Contains(*evidence, "https://fixture.gupy.io/") || !strings.Contains(*evidence, "https://example.com/policy") {
						t.Fatal("publisher policy provenance differs")
					}
				} else if strings.HasPrefix(mode, "gone") {
					if reserved || failures != 0 || gone != 1 || result.Cycle.Status != "gone_pending" {
						t.Fatal("provider disappearance differs", result.Cycle)
					}
				} else if mode == "empty-listing" {
					if reserved || failures != 0 || gone != 0 {
						t.Fatal("empty inventory bypassed absence guard")
					}
				} else if reserved || failures != 1 || gone != 0 {
					t.Fatal("failure became inventory or publisher/gone evidence", result.Cycle)
				}
			}
			assertRichDeadlineAndLease(t, f, "gupy")
		})
	}
}
