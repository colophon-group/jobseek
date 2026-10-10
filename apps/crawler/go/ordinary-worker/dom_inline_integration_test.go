package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealStaticInlineDOMCanonicalSettlementAndFailureAuthority(t *testing.T) {
	for _, family := range []string{"onclick", "script-url", "script-rich"} {
		for _, mode := range []string{"complete", "drift", "reserved"} {
			t.Run(family+"/"+mode, func(t *testing.T) {
				md := map[string]any{"scraper_type": "json-ld"}
				if family == "onclick" {
					md["onclick_selector"] = "tr.item"
				} else {
					v := map[string]any{"variable": "jobs", "url_field": "link", "url_template": "{value}"}
					if family == "script-rich" {
						v["title_field"] = "title"
						v["locations_field"] = "locations"
						md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
					}
					md["script_json_links"] = v
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, "dom", string(raw))
				ctx := context.Background()
				source := "https://example.com/jobs/" + f.company
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					target := source
					if mode == "drift" {
						target = "https://foreign.example/jobs/one"
					}
					if family == "onclick" {
						fmt.Fprintf(w, `<table><tr class="item" onclick="window.location='%s';"><td>Role</td></tr></table>`, target)
					} else if family == "script-rich" {
						fmt.Fprintf(w, `<script>const jobs=[{"link":%q,"title":"Software Engineer","locations":["Zurich"]}];</script>`, target)
					} else {
						fmt.Fprintf(w, `<script>const jobs=[{"link":%q}];</script>`, target)
					}
				}))
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("inline cycle failed to settle", err, result)
				}
				assertRichDeadlineAndLease(t, f, "dom")
				var active, reserved bool
				var failures, empty, gone int
				if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,empty_check_count,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &empty, &gone); err != nil {
					t.Fatal(err)
				}
				if mode != "complete" {
					if !active || empty != 0 || gone != 0 || result.Batches.Inserted != 0 || reserved != (mode == "reserved") || failures != map[string]int{"drift": 1, "reserved": 0}[mode] {
						t.Fatal("failed inline inventory acquired absence/write authority", result, active, reserved, failures, empty, gone)
					}
					return
				}
				var id string
				if err := f.pg.QueryRow(ctx, "SELECT id::text FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id); err != nil {
					t.Fatal(err)
				}
				if result.Batches.Inserted != 1 || failures != 0 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("complete inline inventory lost detail intent", result)
				}
				if family == "script-rich" {
					var title string
					if err := f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", id).Scan(&title); err != nil || title != "Software Engineer" {
						t.Fatal("rich script title lost", err)
					}
				}
			})
		}
	}
}
