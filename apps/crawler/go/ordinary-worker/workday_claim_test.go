package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestRealOwnedWorkdayURLMonitorPersistsStubsEnqueuesDetailsAndPreservesFourMissGuard(t *testing.T) {
	for _, missing := range []int{0, 3} {
		t.Run(fmt.Sprint(missing), func(t *testing.T) {
			f := privateRichPipelineFixture(t, "workday", `{"all_sites":false,"scraper_type":"workday","_monitor_config_fingerprint":"fixture"}`)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=$2 WHERE id=$1::uuid", f.original, missing); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "POST" || r.URL.Path != "/wday/cxs/fixture/Careers/jobs" {
					t.Error("monitor made a detail request or wrong list request")
				}
				w.Header().Set("Content-Type", "application/json")
				fmt.Fprint(w, `{"total":1,"jobPostings":[{"externalPath":"/job/City/Engineer_R100"}]}`)
			}))
			preparer := &pipelinePreparer{}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || result.Batches.Inserted != 1 || preparer.at != 0 || calls != 1 {
				t.Fatalf("owned URL-only claim: %+v / %v", result, err)
			}
			var id, board string
			var titles, locales []string
			var due *time.Time
			var descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT id::text,board_id::text,titles,locales,next_scrape_at,
 (SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE source_url=$1`, "https://fixture.wd1.myworkdayjobs.com/Careers/job/City/Engineer_R100").Scan(&id, &board, &titles, &locales, &due, &descriptions); err != nil {
				t.Fatal(err)
			}
			if board != f.board || len(titles) != 0 || len(locales) != 0 || due == nil || descriptions != 0 {
				t.Fatal("monitor wrote detail content or lost separate detail due")
			}
			config := f.r.HGetAll(ctx, "scrape:"+id).Val()
			if config["board_id"] != f.board || config["scrape_step"] != "0" || config["description_r2_hash"] != "" {
				t.Fatal("detail config not canonical")
			}
			if score, err := f.r.ZScore(ctx, "ft_scrapes_simple:fixture.wd1.myworkdayjobs.com", id).Result(); err != nil || score != 0 {
				t.Fatal("detail not separately urgent", err)
			}
			var active bool
			var count int
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &count); err != nil {
				t.Fatal(err)
			}
			if count != missing+1 || active != (missing < 3) {
				t.Fatal("URL-only monitor did not retain four-miss absence threshold")
			}
			var canonicalDue time.Time
			if err := f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&canonicalDue); err != nil {
				t.Fatal(err)
			}
			if score, err := f.r.ZScore(ctx, "monitors_simple:workday", f.board).Result(); err != nil || score != float64(canonicalDue.UnixMicro())/1e6 || !canonicalDue.Equal(*result.Cycle.Receipt.NextDue()) {
				t.Fatal("monitor receipt/deadline/queue differ", err)
			}
		})
	}
}

func TestRealOwnedWorkdayMonitorPublisherReservationBeforeStatusAndInventory(t *testing.T) {
	for _, robots := range []bool{false, true} {
		t.Run(fmt.Sprint(robots), func(t *testing.T) {
			allSites := "false"
			if robots {
				allSites = "true"
			}
			f := privateRichPipelineFixture(t, "workday", `{"all_sites":`+allSites+`,"scraper_type":"workday"}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if robots && r.URL.Path != "/robots.txt" {
					t.Error("reserved robots still fetched list")
				}
				w.Header().Set("TDM-Reservation", "1")
				w.Header().Set("TDM-Policy", "publisher-policy")
				w.WriteHeader(404)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || !result.Settled || result.Cycle.Status != "publisher_reserved" || calls != 1 {
				t.Fatalf("reservation: %+v / %v", result, err)
			}
			var reserved, active bool
			var failures, postings int
			var evidence string
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,tdm_reservation::text FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &evidence); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&postings); err != nil {
				t.Fatal(err)
			}
			if !reserved || !active || failures != 0 || postings != 1 || !strings.Contains(evidence, "publisher-policy") {
				t.Fatal("reservation became empty success, gone, or generic failure")
			}
		})
	}
}
