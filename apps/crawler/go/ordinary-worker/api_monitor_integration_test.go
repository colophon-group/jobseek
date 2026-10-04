package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

func apiMonitorPage(provider string, later bool) string {
	if provider == "workable" {
		if later {
			return `{"results":[{"shortcode":"A"}],"nextPage":"next-token"}`
		}
		return `{"results":[{"shortcode":"A"}]}`
	}
	if !later {
		return `{"content":[{"id":"A"}],"totalFound":1}`
	}
	rows := []map[string]string{}
	for i := 0; i < 100; i++ {
		rows = append(rows, map[string]string{"id": fmt.Sprintf("A%d", i)})
	}
	body, _ := json.Marshal(map[string]any{"content": rows, "totalFound": 101})
	return string(body)
}

func TestRealOwnedAPIMonitorsPersistOnlyURLsAndScheduleSeparateDetails(t *testing.T) {
	for _, provider := range []string{"smartrecruiters", "workable"} {
		for _, missing := range []int{0, 3} {
			t.Run(fmt.Sprintf("%s/%d", provider, missing), func(t *testing.T) {
				f := privateRichPipelineFixture(t, provider, `{"scraper_type":"`+provider+`"}`)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=$2 WHERE id=$1::uuid", f.original, missing); err != nil {
					t.Fatal(err)
				}
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if provider == "workable" && (r.Method != "POST" || r.URL.Path != "/api/v3/accounts/fixture/jobs") || provider == "smartrecruiters" && (r.Method != "GET" || r.URL.Path != "/v1/companies/fixture/postings" || r.URL.Query().Get("limit") != "100") {
						t.Error("URL monitor changed list request or fetched a detail")
					}
					w.Header().Set("Content-Type", "application/json")
					fmt.Fprint(w, apiMonitorPage(provider, false))
				}))
				preparer := &pipelinePreparer{}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
				if err != nil || !result.Settled || result.Batches.Inserted != 1 || preparer.at != 0 || calls != 1 {
					t.Fatalf("API URL inventory did not settle: %+v %v", result, err)
				}
				postingURL, host := "https://jobs.smartrecruiters.com/fixture/A", "jobs.smartrecruiters.com"
				if provider == "workable" {
					postingURL, host = "https://apply.workable.com/fixture/j/A/", "apply.workable.com"
				}
				var id string
				var titles []string
				var due *time.Time
				var descriptions int
				if err := f.pg.QueryRow(ctx, `SELECT id::text,titles,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE source_url=$1`, postingURL).Scan(&id, &titles, &due, &descriptions); err != nil {
					t.Fatal(err)
				}
				if len(titles) != 0 || due == nil || descriptions != 0 || f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board || f.r.HGet(ctx, "scrape:"+id, "scrape_step").Val() != "0" {
					t.Fatal("monitor wrote detail content or failed separate detail configuration")
				}
				if score, err := f.r.ZScore(ctx, "ft_scrapes_simple:"+host, id).Result(); err != nil || score != 0 {
					t.Fatal("separate detail did not get its first-time queue", err)
				}
				var active bool
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &count); err != nil || count != missing+1 || active != (missing < 3) {
					t.Fatal("URL-only four-miss lifecycle guard changed", err)
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}

func TestRealOwnedAPIMonitorLaterFailureAndPublisherReservationConserveInventory(t *testing.T) {
	for _, provider := range []string{"smartrecruiters", "workable"} {
		for _, mode := range []string{"later_404", "later_reserved", "header_404", "meta"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				f := privateRichPipelineFixture(t, provider, `{"scraper_type":"`+provider+`"}`)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if strings.HasPrefix(mode, "later_") && calls == 1 {
						fmt.Fprint(w, apiMonitorPage(provider, true))
						return
					}
					if strings.HasPrefix(mode, "later_") {
						if provider == "smartrecruiters" && r.URL.Query().Get("offset") != "100" {
							t.Error("wrong next-page offset")
						}
						if provider == "workable" {
							var payload map[string]any
							if json.NewDecoder(r.Body).Decode(&payload) != nil || payload["token"] != "next-token" {
								t.Error("opaque next-page token lost")
							}
						}
					}
					w.Header().Set("TDM-Policy", "publisher-policy")
					if mode == "meta" {
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="publisher-policy">`)
					} else {
						if mode != "later_404" {
							w.Header().Set("TDM-Reservation", "1")
						}
						w.WriteHeader(404)
					}
				}))
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
				if mode == "later_404" {
					if err != nil || !result.Settled || !result.DiscoveryError || result.Cycle.Status != "failed" {
						t.Fatalf("failed page became partial success/gone: %+v %v", result, err)
					}
				} else if err != nil || !result.Settled || result.Cycle.Status != "publisher_reserved" {
					t.Fatalf("positive reservation not durable: %+v %v", result, err)
				}
				wantCalls := 1
				if strings.HasPrefix(mode, "later_") {
					wantCalls = 2
				}
				if calls != wantCalls {
					t.Fatal("extra fetch or failed pagination boundary", calls)
				}
				var active, reserved bool
				var count, failures, postings int
				var evidence *string
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &count); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence, &postings); err != nil {
					t.Fatal(err)
				}
				if !active || count != 0 || postings != 1 || reserved != (mode != "later_404") || failures != map[bool]int{true: 1, false: 0}[mode == "later_404"] {
					t.Fatal("partial inventory wrote, tombstoned, or changed wrong failure budget")
				}
				if reserved && (evidence == nil || !strings.Contains(*evidence, "publisher-policy") || mode == "meta" && !strings.Contains(*evidence, `"source": "meta"`)) {
					t.Fatal("publisher evidence lost its source/policy")
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}

func TestRealOwnedWorkableMonitor429UsesCountedFallbackThroughVerifiedHTTP(t *testing.T) {
	f := privateRichPipelineFixture(t, "workable", `{"scraper_type":"workable"}`)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	claim, circuits := claimFixture(t, f)
	calls, posts := 0, 0
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		switch r.Host + r.URL.Path {
		case "apply.workable.com/api/v3/accounts/fixture/jobs":
			if r.Method != "POST" {
				t.Error("list request lost POST")
			}
			posts++
			w.WriteHeader(429)
		case "apply.workable.com/fixture/llms.txt":
			fmt.Fprint(w, "All open roles at Fixture: 1 current openings")
		case "apply.workable.com/fixture/jobs.md":
			fmt.Fprint(w, "Use the search endpoint to filter results")
		case "www.workable.com/api/accounts/fixture":
			fmt.Fprint(w, `{"jobs":[{"shortcode":"A"}]}`)
		default:
			t.Error("unexpected fallback resource", r.URL)
			w.WriteHeader(404)
		}
	}))
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
	if err != nil || !result.Settled || result.Batches.Inserted != 1 || posts != 4 || calls != 7 || result.HTTP.Requests != 7 || result.HTTP.LastHost != "www.workable.com" {
		t.Fatalf("existing retries/counted fallback lost: %+v %v posts=%d calls=%d", result, err, posts, calls)
	}
	assertRichDeadlineAndLease(t, f, "workable")
}
