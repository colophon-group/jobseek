package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOwnedOraclePersistsRichInventoryAndSettlesFailuresAndReservations(t *testing.T) {
	for _, mode := range []string{"complete", "later404", "later_reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "oracle_hcm", `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","scraper_type":"oracle_hcm","scraper_config":{}}`)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("native Oracle claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			rows := []any{map[string]any{"Id": f.company, "Title": "Senior Software Engineer", "PrimaryLocation": "Zurich", "JobSchedule": "Full-time", "PostedDate": "2026-10-05"}}
			total := 1
			if mode != "complete" {
				total, rows = 201, []any{}
				for n := 1; n <= 200; n++ {
					rows = append(rows, map[string]any{"Id": n, "Title": "Engineer"})
				}
			}
			payload, _ := json.Marshal(map[string]any{"items": []any{map[string]any{"TotalJobsCount": total, "requisitionList": rows}}})
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "fixture.fa.em2.oraclecloud.com" || r.URL.Path != "/hcmRestApi/resources/latest/recruitingCEJobRequisitions" {
					t.Error("Oracle finder binding changed")
				}
				if calls == 1 {
					w.Write(payload)
					return
				}
				if !strings.HasSuffix(r.URL.RawQuery, ",offset=200") {
					t.Error("Oracle finder offset changed")
				}
				if mode == "later404" {
					w.WriteHeader(404)
					return
				}
				w.Header().Set("TDM-Reservation", "1")
				w.Header().Set("TDM-Policy", "https://fixture.fa.em2.oraclecloud.com/policy")
				w.WriteHeader(503)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("Oracle ownership did not settle", result, err)
			}
			assertRichDeadlineAndLease(t, f, "oracle_hcm")
			var reserved bool
			var gone, failures int
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &gone, &failures); err != nil {
				t.Fatal(err)
			}
			if gone != 0 || reserved != (mode == "later_reserved") || failures != map[string]int{"complete": 0, "later404": 1, "later_reserved": 0}[mode] {
				t.Fatal("Oracle publisher/failure behavior changed", reserved, gone, failures)
			}
			if mode != "complete" {
				var count, active int
				if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 || calls != 2 {
					t.Fatal("partial/opted-out Oracle inventory changed postings", err)
				}
				return
			}
			var title, employment string
			var locations []int32
			url := "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/" + f.company
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],employment_type,location_ids FROM job_posting WHERE source_url=$1", url).Scan(&title, &employment, &locations); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || employment != "full_time" || fmt.Sprint(locations) != "[2]" || calls != 1 || result.Batches.Inserted != 1 || result.Cycle.Gone != 1 {
				t.Fatal("Oracle canonical fields/lifecycle differ", title, employment, locations, result)
			}
		})
	}
}
