package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealAutomaticAPIArrayProjectionAndAbsenceEvidence(t *testing.T) {
	for _, mode := range []string{"root", "wrapped", "small-root", "literal-empty", "unproved-empty", "small-wrapper", "exact-empty", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"api_url": "https://example.com/api", "url_field": "url", "fields": map[string]any{"title": "title", "description": "body", "locations": "city"}, "scraper_type": "skip"}
			if mode == "exact-empty" {
				md["empty_response"] = map[string]any{"jobs": []any{}, "count": 0}
			}
			raw, _ := json.Marshal(md)
			f := privateRichPipelineFixture(t, "api_sniffer", string(raw))
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "example.com" || r.URL.Path != "/api" {
					t.Error("unbound API request")
				}
				rows := []any{}
				n := 3
				if mode == "small-root" || mode == "small-wrapper" {
					n = 1
				}
				if mode == "literal-empty" || mode == "unproved-empty" || mode == "exact-empty" {
					n = 0
				}
				for i := 0; i < n; i++ {
					rows = append(rows, map[string]any{"url": fmt.Sprintf("https://example.com/job/%s/%d", f.company, i), "title": "Senior Software Engineer", "body": "<p>Build Go systems in Zurich.</p>", "city": "Zurich"})
				}
				var payload any = rows
				if mode == "wrapped" || mode == "small-wrapper" || mode == "unproved-empty" {
					payload = map[string]any{"jobs": rows}
				}
				if mode == "exact-empty" {
					payload = map[string]any{"jobs": rows, "count": 0}
				}
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
				}
				json.NewEncoder(w).Encode(payload)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled || calls != 1 {
				t.Fatal(err, result, calls)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
			want := 0
			if mode == "root" || mode == "wrapped" {
				want = 3
			}
			if mode == "small-root" {
				want = 1
			}
			if result.Batches.Inserted != want {
				t.Fatal("partial inventory or field inference", result.Batches, want)
			}
			if want > 0 {
				var n int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid AND p.titles[1]='Senior Software Engineer' AND p.location_ids=ARRAY[2] AND d.html LIKE '%Build Go systems in Zurich.%' AND NOT d.r2_uploaded", f.board, f.original).Scan(&n); err != nil || n != want {
					t.Fatal("canonical fields/description intent", n, err)
				}
			} else {
				var active, reserved bool
				var title string
				var failures int
				if err := f.pg.QueryRow(ctx, "SELECT p.is_active,p.titles[1],b.consecutive_failures,b.tdm_reserved FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid", f.original).Scan(&active, &title, &failures, &reserved); err != nil {
					t.Fatal(err)
				}
				failed := mode == "unproved-empty" || mode == "small-wrapper"
				if !active || title != "Original" || failed && failures != 1 || reserved != (mode == "reserved") {
					t.Fatal("absence/policy discarded canonical state", active, title, failures, reserved)
				}
			}
		})
	}
}
