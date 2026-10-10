package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"reflect"
	"testing"
)

func TestRealOriginalAPIConvergenceCanonicalSettlement(t *testing.T) {
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_pagination_convergence.json")
	var cases []struct {
		Name      string
		BoardURL  string `json:"board_url"`
		Metadata  json.RawMessage
		Responses []json.RawMessage
		Requests  []struct{ Method, URL, Body string }
		Jobs      []map[string]any
		Truncated bool
	}
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 15 {
		t.Fatal("original convergence corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "api_sniffer", string(c.Metadata), c.BoardURL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			cursor := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if cursor >= len(c.Responses) {
					t.Error("extra convergence request")
					w.WriteHeader(400)
					return
				}
				want := c.Requests[cursor]
				expected, err := url.Parse(want.URL)
				if err != nil || r.Method != want.Method || r.Host != expected.Host || r.URL.Path != expected.Path || !reflect.DeepEqual(r.URL.Query(), expected.Query()) {
					t.Error("original request changed", cursor)
				}
				w.Header().Set("Content-Type", "application/json")
				w.Write(c.Responses[cursor])
				cursor++
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled || cursor != len(c.Requests) {
				t.Fatal("convergence did not settle with original requests", err, cursor, len(c.Requests))
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
			var count, failures, empty, missing int
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if count != len(c.Jobs) || result.Batches.Inserted != len(c.Jobs) || failures != 0 {
				t.Fatal("canonical prefix/accounting differs", count, len(c.Jobs), failures)
			}
			if c.Truncated && (missing != 0 || empty != 0 || result.Cycle.Gone != 0) {
				t.Fatal("unproved inventory acquired absence authority")
			}
			if len(c.Jobs) == 0 && empty != 1 {
				t.Fatal("proved empty inventory lost normal empty accounting")
			}
		})
	}
}

func TestRealAPIConvergenceUnprovedZeroUsesFailureSettlement(t *testing.T) {
	md := `{"api_url":"https://example.com/api?page=1","json_path":"jobs","total_path":"total","url_field":"url","pagination":{"style":"page","param_name":"page","max_pages":3},"pagination_convergence":{"max_passes":3,"required_no_growth_passes":2,"identity_by":["url"]},"scraper_type":"skip"}`
	for _, mode := range []string{"missing_total", "invalid_shape", "boolean_total", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "api_sniffer", md)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_board SET empty_check_count=3 WHERE id=$1::uuid", f.board); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					return
				}
				body := map[string]any{"jobs": []any{}}
				if mode == "boolean_total" {
					body["total"] = false
				}
				if mode == "invalid_shape" {
					body["jobs"] = map[string]any{}
					body["total"] = 0
				}
				json.NewEncoder(w).Encode(body)
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("unproved zero did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
			var failures, empty, missing int
			var active, reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty, &reserved); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
				t.Fatal(err)
			}
			want := 1
			if mode == "reserved" {
				want = 0
			}
			if failures != want || empty != 3 || !active || missing != 0 || reserved != (mode == "reserved") || result.Batches.Inserted != 0 {
				t.Fatal("unproved zero acquired absence or lost failure/publisher precedence", failures, empty, active, missing, reserved)
			}
		})
	}
}
