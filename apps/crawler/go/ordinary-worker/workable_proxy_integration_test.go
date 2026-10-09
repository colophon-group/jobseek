package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealWorkableProxyMonitorUsesCredentialedHopAndSeparateDetails(t *testing.T) {
	for _, mode := range []string{"complete", "malformed", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "workable", `{"proxy":true,"scraper_type":"workable"}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			origin := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Host != "apply.workable.com" && r.Host != "www.workable.com" {
					t.Error("foreign proxy destination", r.Host)
					w.WriteHeader(400)
					return
				}
				if strings.HasPrefix(r.URL.Path, "/api/v3/") {
					if r.Method != "POST" {
						t.Error("list method changed")
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(404)
						return
					}
					if mode == "malformed" {
						fmt.Fprint(w, `{"results":"invalid-list"}`)
						return
					}
					fmt.Fprint(w, apiMonitorPage("workable", false))
					return
				}
				w.WriteHeader(404)
			}))
			client := credentialedProxyFixture(t, origin)
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || preparer.at != 0 {
				t.Fatal("proxy monitor did not settle URL-only inventory", result, err)
			}
			var reserved bool
			var failures, missing int
			if err := f.pg.QueryRow(ctx, "SELECT b.tdm_reserved,b.consecutive_failures,p.missing_count FROM job_board b JOIN job_posting p ON p.board_id=b.id WHERE p.id=$1::uuid", f.original).Scan(&reserved, &failures, &missing); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				var due *time.Time
				var id string
				if err := f.pg.QueryRow(ctx, "SELECT id::text,next_scrape_at FROM job_posting WHERE source_url=$1", "https://apply.workable.com/fixture/j/A/").Scan(&id, &due); err != nil || due == nil {
					t.Fatal("proxy inventory lost independent detail schedule", err)
				}
				if f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board || result.Batches.Inserted != 1 || missing != 1 {
					t.Fatal("proxy URL-only effects differ", result, missing)
				}
			} else if missing != 0 || reserved != (mode == "reserved") || mode == "malformed" && failures != 1 || result.Batches.Inserted != 0 {
				t.Fatal("proxy failure/reservation changed absence or content", result, missing, failures, reserved)
			}
			assertRichDeadlineAndLease(t, f, "workable")
		})
	}
}
