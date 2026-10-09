package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealOwnedDOMCompleteInventoryRetryGoneAndPolicy(t *testing.T) {
	realDOMTransportCases(t, false)
}
func TestRealProxyDOMCompleteInventoryRetryGoneAndPolicy(t *testing.T) {
	realDOMTransportCases(t, true)
}
func realDOMTransportCases(t *testing.T, proxy bool, annotations ...bool) {
	annotated := len(annotations) > 0 && annotations[0]
	for _, mode := range []string{"success", "retry429", "empty", "retry403", "gone404", "gone410", "challenge", "header", "meta", "redirect_header"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "dom", proxyFixtureMetadata(t, sharedAnnotationFixtureMetadata(t, `{"url_filter":{"include":"/jobs/\\w+","exclude":"intern"},"scraper_type":"json-ld"}`, annotated), proxy))
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.Host != "example.com" || r.URL.Path != "/careers" && r.URL.Path != "/policy-page" {
					t.Error("DOM left reviewed direct resource")
				}
				if mode == "retry429" && calls.Load() < 3 {
					w.WriteHeader(429)
					return
				}
				switch mode {
				case "empty":
					w.WriteHeader(200)
				case "gone404":
					w.WriteHeader(404)
				case "gone410":
					w.WriteHeader(410)
				case "retry403":
					w.WriteHeader(403)
				case "challenge":
					fmt.Fprint(w, `<html><title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div></html>`)
				case "redirect_header":
					if r.URL.Path == "/careers" {
						http.Redirect(w, r, "/policy-page", 302)
						return
					}
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "dom-policy")
					w.WriteHeader(404)
				case "header":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "dom-policy")
					w.WriteHeader(404)
				case "meta":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="dom-policy">`)
				default:
					fmt.Fprint(w, `<a href="/jobs/工程師">Engineer</a><a href="/jobs/工程師">Duplicate</a><a href="/jobs/intern">Intern</a><a href="/contact">Contact</a><a href="/careers#jobs">Self</a><script>var hidden = '<a href="/jobs/hidden">Hidden</a>';</script>`)
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
				if mode == "header" || mode == "meta" || mode == "redirect_header" {
					wantCalls := int64(1)
					resource := "https://example.com/careers"
					if mode == "redirect_header" {
						wantCalls = 2
						resource = "https://example.com/policy-page"
					}
					if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || calls.Load() != wantCalls || evidence == nil || !strings.Contains(*evidence, resource) || !strings.Contains(*evidence, "dom-policy") {
						t.Fatal("policy/resource lost", result.Cycle, evidence)
					}
				} else if strings.HasPrefix(mode, "gone") {
					if result.Cycle.Status != "gone_pending" || failures != 0 || reserved || calls.Load() != 1 {
						t.Fatal("DOM disappearance changed failure budget", result.Cycle)
					}
				} else {
					want := int64(1)
					if mode == "empty" || mode == "retry403" {
						want = 3
					}
					if result.Cycle.Status != "failed" || failures != 1 || reserved || calls.Load() != want {
						t.Fatal("failure became inventory/provider disappearance", result.Cycle, calls.Load())
					}
				}
			}
			assertRichDeadlineAndLease(t, f, "dom")
		})
	}
}
