package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealRichPaginationRequiresExplicitCompleteTail(t *testing.T) {
	for _, mode := range []string{"complete", "empty", "unproved-empty", "repeat", "max-pages", "tail-403", "tail-policy"} {
		t.Run(mode, func(t *testing.T) {
			maxPages := 4
			if mode == "max-pages" {
				maxPages = 2
			}
			md, _ := json.Marshal(map[string]any{"scraper_type": "skip", "transport_attempts": 1, "rich_rows": map[string]any{"row_selector": "article", "link_selector": "a"}, "pagination": map[string]any{"param_name": "offset", "start": 0, "increment": 20, "max_pages": maxPages, "transport_attempts": 1, "transient_403": true}, "empty_states": []any{map[string]any{"selector": ".empty", "exact_text": "No jobs", "forbidden_link_selector": "article a"}}})
			f := privateRichPipelineFixture(t, "dom", string(md))
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if calls > 1 && r.URL.Query().Get("offset") != fmt.Sprint((calls-1)*20) {
					t.Error("cursor changed")
				}
				if mode == "empty" || calls == 3 && mode == "complete" {
					fmt.Fprint(w, `<div class="empty">No jobs</div>`)
					return
				}
				if calls == 2 {
					switch mode {
					case "unproved-empty":
						fmt.Fprint(w, "<p>Unavailable</p>")
						return
					case "tail-403":
						w.WriteHeader(403)
						return
					case "tail-policy":
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					}
				}
				n := calls
				if mode == "repeat" {
					n = 1
				}
				fmt.Fprintf(w, `<article><a href="https://example.com/jobs/%s-%d">Engineer</a></article>`, f.company, n)
			}))
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("settlement", e)
			}
			var inserted, missing, failures int
			var reserved bool
			if e = f.pg.QueryRow(ctx, `SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid),(SELECT missing_count FROM job_posting WHERE id=$2::uuid) FROM job_board WHERE id=$1::uuid`, f.board, f.original).Scan(&failures, &reserved, &inserted, &missing); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" || mode == "empty" || mode == "repeat" {
				want, wantMissing := 2, 1
				if mode == "repeat" {
					want = 1 // Original static pagination stops when no new URLs appear.
				}
				if mode == "empty" {
					want, wantMissing = 0, 0
				}
				if inserted != want || missing != wantMissing || failures != 0 || reserved {
					t.Fatal("complete inventory differs", inserted, missing, failures)
				}
			} else if inserted != 0 || missing != 0 || reserved != (mode == "tail-policy") || failures != map[bool]int{true: 0, false: 1}[mode == "tail-policy"] {
				t.Fatal("failed prefix changed jobs", inserted, missing, failures)
			}
			assertRichDeadlineAndLease(t, f, "dom")
		})
	}
}
