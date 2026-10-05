package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealOwnedICIMSInventoryRetryPolicyAndGone(t *testing.T) {
	for _, mode := range []string{"success", "cookie_pages", "duplicate_partial", "later_empty", "peer_failure", "peer_policy", "header", "meta", "non200_policy", "gone404", "gone410", "redirect", "empty", "retry202", "retry401", "retry429", "challenge", "jibe", "jibe404", "jibe_invalid_json", "jibe_drift"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"host":"careers-native.icims.com","scraper_type":"json-ld"}`
			if strings.HasPrefix(mode, "peer_") {
				metadata = `{"host":"careers-native.icims.com","scraper_type":"json-ld","dedupe_job_ids_from_hosts":["careers-peer.icims.com"]}`
			}
			if strings.HasPrefix(mode, "jibe") {
				metadata = `{"host":"careers-native.icims.com","scraper_type":"json-ld","jibe_url":"https://careers.example.com/jobs","jibe_job_hosts":["careers-native.icims.com"]}`
			}
			f := privateRichPipelineFixture(t, "icims", metadata)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := calls.Add(1)
				if r.Method != "GET" {
					t.Error("non GET iCIMS request")
				}
				if mode == "jibe404" {
					w.WriteHeader(404)
					return
				}
				if strings.HasPrefix(mode, "jibe") {
					if r.Host == "careers-native.icims.com" {
						fmt.Fprint(w, `<script type="text/javascript">window.top.location.href='https://careers.example.com/jobs';</script>`)
						return
					}
					if r.Host != "careers.example.com" || r.URL.Path != "/api/jobs" {
						t.Error("unreviewed Jibe resource")
					}
					if mode == "jibe_invalid_json" && n < 4 {
						fmt.Fprint(w, `[]`)
						return
					}
					if mode == "jibe_drift" {
						if r.URL.Query().Get("page") == "1" {
							fmt.Fprint(w, `{"totalCount":2,"jobs":[{"data":{"slug":"101","apply_url":"https://careers-native.icims.com/jobs/101/login"}}]}`)
						} else {
							fmt.Fprint(w, `{"totalCount":1,"jobs":[]}`)
						}
						return
					}
					fmt.Fprint(w, `{"totalCount":1,"jobs":[{"data":{"slug":"101","apply_url":"https://careers-native.icims.com/jobs/101/login"}}]}`)
					return
				}
				if r.URL.Path != "/jobs/search" || r.Host != "careers-native.icims.com" && r.Host != "careers-peer.icims.com" {
					t.Error("unreviewed listing resource")
				}
				if r.Host == "careers-peer.icims.com" {
					if mode == "peer_policy" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "icims-policy")
						fmt.Fprint(w, "policy")
					} else {
						w.WriteHeader(404)
					}
					return
				}
				if strings.HasPrefix(mode, "retry") && n < 3 {
					status := map[string]int{"retry202": 202, "retry401": 401, "retry429": 429}[mode]
					w.WriteHeader(status)
					return
				}
				switch mode {
				case "gone404":
					w.WriteHeader(404)
					return
				case "gone410":
					w.WriteHeader(410)
					return
				case "non200_policy":
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(403)
					return
				case "redirect":
					http.Redirect(w, r, "https://untrusted.example.com/jobs", 302)
					return
				case "header":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "icims-policy")
					fmt.Fprint(w, "not a listing")
					return
				case "meta":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="icims-policy">`)
					return
				case "empty":
					return
				case "challenge":
					fmt.Fprint(w, `<html><title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div></html>`)
					return
				}
				if mode == "cookie_pages" || mode == "duplicate_partial" || mode == "later_empty" {
					if r.URL.Query().Get("pr") == "" {
						http.SetCookie(w, &http.Cookie{Name: "page_session", Value: "accepted", Path: "/"})
						fmt.Fprint(w, `<div class="iCIMS_ListingsPage">Page 1 of 2<a href="/jobs/101/job">One</a></div>`)
						return
					}
					if cookie, err := r.Cookie("page_session"); err != nil || cookie.Value != "accepted" {
						t.Error("sequential listing cookie lost")
					}
					if mode == "later_empty" {
						fmt.Fprint(w, `<div class="iCIMS_ListingsPage">Page 2 of 2</div>`)
						return
					}
					id := 102
					if mode == "duplicate_partial" {
						id = 101
					}
					fmt.Fprintf(w, `<div class="iCIMS_ListingsPage">Page 2 of 2<a href="/jobs/%d/job">Two</a></div>`, id)
					return
				}
				fmt.Fprint(w, `<div class="iCIMS_ListingsPage"><a href="/jobs/101/engineer/job">One</a></div>`)
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
			complete := mode == "success" || mode == "cookie_pages" || strings.HasPrefix(mode, "retry") || mode == "jibe" || mode == "jibe_invalid_json"
			policy := mode == "header" || mode == "meta" || mode == "peer_policy"
			if complete || mode == "duplicate_partial" {
				count := 1
				if mode == "cookie_pages" {
					count = 2
				}
				if result.Batches.Inserted != count || postings != 1+count || reserved || failures != 0 {
					t.Fatal("inventory write changed", result)
				}
				if mode == "duplicate_partial" {
					if !active || missing != 3 {
						t.Fatal("partial inventory delisted missing posting")
					}
				} else if active || missing != 4 {
					t.Fatal("complete inventory did not reconcile absence")
				}
				if n := f.r.ZCard(ctx, "ft_scrapes_simple:careers-native.icims.com").Val(); n != int64(count) {
					t.Fatal("details not scheduled", n)
				}
			} else {
				if !active || missing != 3 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("failed/reserved discovery published partial inventory")
				}
				if policy {
					if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || evidence == nil || !strings.Contains(*evidence, "icims-policy") {
						t.Fatal("policy lost", result.Cycle, evidence)
					}
					if mode == "peer_policy" && !strings.Contains(*evidence, "careers-peer.icims.com") {
						t.Fatal("peer evidence lost")
					}
				} else if mode == "gone404" || mode == "gone410" {
					if result.Cycle.Status != "gone_pending" || reserved || failures != 0 || calls.Load() != 1 {
						t.Fatal("primary gone became failure", result.Cycle)
					}
				} else if result.Cycle.Status != "failed" || reserved || failures != 1 {
					t.Fatal("invalid inventory became success/gone/reservation", result.Cycle)
				}
			}
			expected := int64(1)
			if mode == "cookie_pages" || mode == "duplicate_partial" || mode == "later_empty" || strings.HasPrefix(mode, "peer_") || mode == "jibe" {
				expected = 2
			}
			if mode == "empty" || mode == "non200_policy" || strings.HasPrefix(mode, "retry") || mode == "jibe_drift" {
				expected = 3
			}
			if mode == "jibe_invalid_json" {
				expected = 4
			}
			if calls.Load() != expected {
				t.Fatal("retry/pagination contract changed", mode, calls.Load(), expected)
			}
			assertRichDeadlineAndLease(t, f, "icims")
		})
	}
}
