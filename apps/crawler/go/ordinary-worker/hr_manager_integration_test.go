package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
)

func TestRealHRManagerHTTPJoinPersistsIdentityAndRejectsMismatchedInventory(t *testing.T) {
	var corpus struct {
		Cases []struct{ Name, Board, Feed string }
	}
	raw, err := os.ReadFile("testdata/python_hr_manager.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil {
		t.Fatal(err)
	}
	valid := corpus.Cases[0]
	for _, assignment := range []string{"skip", "json-ld"} {
		for _, mode := range []string{"complete", "missing-position-feed", "duplicate-feed-id", "wrong-tenant", "malformed-feed", "missing-feed-link", "board-reserved", "feed-reserved", "feed-404"} {
			t.Run(assignment+"/"+mode, func(t *testing.T) {
				boardURL := "https://candidate.hr-manager.net/vacancies/list.aspx?customer=fixture"
				md := `{"preset":"hr_manager","customer":"fixture","scraper_type":"` + assignment + `"}`
				f := privateRichPipelineFixtureURL(t, "rss", md, boardURL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				page, feed := valid.Board, valid.Feed
				for _, c := range corpus.Cases {
					if c.Name == mode {
						page, feed = c.Board, c.Feed
					}
				}
				requests := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					requests++
					if r.Host == "candidate.hr-manager.net" {
						if r.URL.Path != "/vacancies/list.aspx" || r.URL.RawQuery != "customer=fixture" {
							t.Error("HR Manager board binding lost")
						}
						if mode == "board-reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.Header().Set("TDM-Policy", "https://example.com/policy")
						}
						fmt.Fprint(w, page)
					} else if r.Host == "api.hr-manager.net" {
						if r.URL.Path != "/JobPortal.svc/fixture/PositionList/rss/" || r.URL.RawQuery != "protype=RecruitmentProject&incads=true" {
							t.Error("HR Manager feed binding lost")
						}
						if mode == "feed-404" {
							w.WriteHeader(404)
							return
						}
						if mode == "feed-reserved" {
							w.Header().Set("TDM-Reservation", "1")
							w.Header().Set("TDM-Policy", "https://example.com/policy")
						}
						fmt.Fprint(w, feed)
					} else {
						t.Error("HR Manager left its resources")
					}
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("HR Manager cycle did not settle", result, err)
				}
				wantRequests := 2
				if mode == "board-reserved" || mode == "wrong-tenant" {
					wantRequests = 1
				}
				if requests != wantRequests {
					t.Fatal("HR Manager retry/resource behavior changed", requests)
				}
				assertRichDeadlineAndLease(t, f, "rss")
				if mode != "complete" {
					var count, active int
					if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 {
						t.Fatal("failed HR Manager join changed canonical postings", err)
					}
					want := "failed"
					if mode == "board-reserved" || mode == "feed-reserved" {
						want = "publisher_reserved"
					}
					if result.Cycle.Status != want {
						t.Fatal("HR Manager failure/policy contract changed", result.Cycle)
					}
					return
				}
				var title, identity, employment string
				var locations []int32
				if err := f.pg.QueryRow(ctx, "SELECT titles[1],source_identity,employment_type,location_ids FROM job_posting WHERE board_id=$1::uuid AND source_identity=$2", f.board, "hr_manager:fixture:123").Scan(&title, &identity, &employment, &locations); err != nil {
					t.Fatal(err)
				}
				if title != "Senior Engineer" || identity != "hr_manager:fixture:123" || employment != "full_time" || fmt.Sprint(locations) != "[2]" || result.Batches.Inserted != 1 || result.Cycle.Gone != 1 {
					t.Fatal("HR Manager canonical fields/identity differ", result, title, identity, employment, locations)
				}
			})
		}
	}
}
