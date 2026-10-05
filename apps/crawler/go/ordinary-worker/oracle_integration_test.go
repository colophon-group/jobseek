package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOwnedOraclePersistsRichInventoryAndSettlesFailuresAndReservations(t *testing.T) {
	for _, mode := range []string{"complete", "complete_enrich", "complete_enrich_touch", "complete_enrich_relist", "complete_enrich_retained", "complete_enrich_tombstone", "later404", "later_reserved"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","scraper_type":"oracle_hcm","scraper_config":{}}`
			if strings.HasPrefix(mode, "complete_enrich") {
				metadata = `{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","scraper_type":"oracle_hcm","scraper_config":{"enrich":["description","employment_type"]}}`
			}
			f := privateRichPipelineFixture(t, "oracle_hcm", metadata)
			ctx := context.Background()
			url := "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/" + f.company
			existing := strings.HasPrefix(mode, "complete_enrich_")
			retained := mode == "complete_enrich_relist" || mode == "complete_enrich_retained"
			if existing {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=NULL,is_active=$3,scrape_failures=$4,description_r2_hash=$5::bigint WHERE id=$1::uuid", f.original, url, mode != "complete_enrich_relist", map[string]int{"complete_enrich_relist": 3, "complete_enrich_tombstone": 3}[mode], func() any {
					if retained {
						return int64(123)
					}
					return nil
				}()); err != nil {
					t.Fatal(err)
				}
				if retained {
					if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Delegated description</p>',123,true)", f.original); err != nil {
						t.Fatal(err)
					}
				}
			}
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
			if !strings.HasPrefix(mode, "complete") {
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
			if gone != 0 || reserved != (mode == "later_reserved") || failures != map[string]int{"complete": 0, "complete_enrich": 0, "later404": 1, "later_reserved": 0}[mode] {
				t.Fatal("Oracle publisher/failure behavior changed", reserved, gone, failures)
			}
			if !strings.HasPrefix(mode, "complete") {
				var count, active int
				if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 || result.Batches.Inserted != 0 || calls != 2 {
					t.Fatal("partial/opted-out Oracle inventory changed postings", err)
				}
				return
			}
			var title, employment string
			var locations []int32
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],employment_type,location_ids FROM job_posting WHERE source_url=$1", url).Scan(&title, &employment, &locations); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || employment != "full_time" || fmt.Sprint(locations) != "[2]" || calls != 1 || result.Batches.Inserted != map[bool]int{false: 1, true: 0}[existing] || result.Cycle.Gone != map[bool]int{false: 1, true: 0}[existing] {
				t.Fatal("Oracle canonical fields/lifecycle differ", title, employment, locations, result)
			}
			var id string
			var due *time.Time
			if err := f.pg.QueryRow(ctx, "SELECT id::text,next_scrape_at FROM job_posting WHERE source_url=$1", url).Scan(&id, &due); err != nil {
				t.Fatal(err)
			}
			if retained {
				var html string
				var uploaded bool
				if err := f.pg.QueryRow(ctx, "SELECT html,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid AND locale='en'", id).Scan(&html, &uploaded); err != nil || html != "<p>Delegated description</p>" || !uploaded {
					t.Fatal("monitor replaced delegated detail content", err)
				}
			}
			if mode == "complete_enrich_relist" {
				var scrapeFailures int
				if err := f.pg.QueryRow(ctx, "SELECT scrape_failures FROM job_posting WHERE id=$1::uuid", id).Scan(&scrapeFailures); err != nil || due == nil || scrapeFailures != 0 {
					t.Fatal("relist did not restore detail budget", err)
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:fixture.fa.em2.oraclecloud.com", id).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.HGet(ctx, "scrape:"+id, "description_r2_hash").Val() != "123" {
					t.Fatal("relist did not retain canonical hash/deadline", err)
				}
			} else if mode == "complete_enrich" || mode == "complete_enrich_touch" {
				if due == nil {
					t.Fatal("delegated Oracle detail was not scheduled")
				}
				score, err := f.r.ZScore(ctx, "ft_scrapes_simple:fixture.fa.em2.oraclecloud.com", id).Result()
				if err != nil || score != 0 || f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != url {
					t.Fatal("Oracle detail SQL/Redis conservation differs", err)
				}
			} else if due != nil || f.r.Exists(ctx, "scrape:"+id).Val() != 0 {
				t.Fatal("healthy, non-enriched or tombstoned rich Oracle acquired urgent detail schedule")
			}
		})
	}
}
