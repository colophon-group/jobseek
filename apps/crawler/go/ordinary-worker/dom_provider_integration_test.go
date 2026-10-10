package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sync"
	"testing"
)

func TestRealDOMProviderOriginalInventoriesAndPublisherConservation(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_dom_provider_contracts.json")
	var cases []struct {
		sharedServiceOriginalCase
		Status string
	}
	if err != nil || json.Unmarshal(raw, &cases) != nil {
		t.Fatal("original provider oracle unavailable")
	}
	selected := map[string]bool{"prospective-complete": true, "prospective-uppercase-uuid": true, "prospective-zero": true, "prospective-wrong-medium": true, "lucca-complete": true, "lucca-explicit-zero": true, "dualoo-original-preset": true, "inert-marker-lg_portal": true}
	for _, c := range cases {
		if !selected[c.Name] {
			continue
		}
		for _, reserved := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/reserved=%t", c.Name, reserved), func(t *testing.T) {
				md := c.Board.Metadata
				// The monitor inventory is unchanged; this fixture isolates its
				// canonical writes from the independently qualified detail lane.
				md["scraper_type"] = "skip"
				encoded, _ := json.Marshal(md)
				f := privateRichPipelineFixtureURL(t, "dom", string(encoded), c.Board.BoardURL)
				claim, circuits := claimFixture(t, f)
				requests := 0
				var requestsMu sync.Mutex
				used := make([]bool, len(c.Exchanges))
				client := &VerifiedDirectHTTP{client: verifiedLastHTTPFixture(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requestsMu.Lock()
					defer requestsMu.Unlock()
					requests++
					rawURL := "https://" + r.Host + r.URL.String()
					found := -1
					for i, x := range c.Exchanges {
						if !used[i] && r.Method == x.Method && sharedOriginalRequestURLEqual(rawURL, x.URL) {
							found = i
							break
						}
					}
					if found < 0 {
						t.Error("provider request changed")
						w.WriteHeader(400)
						return
					}
					used[found] = true
					if reserved {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(403)
						return
					}
					x := c.Exchanges[found]
					if x.Response.Status != 0 {
						for k, v := range x.Response.Headers {
							w.Header().Set(k, v)
						}
						w.WriteHeader(x.Response.Status)
						fmt.Fprint(w, x.Response.Body)
					} else {
						fmt.Fprint(w, c.Response)
					}
				}))}
				ctx := context.Background()
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				wantRequests := len(c.Exchanges)
				if reserved {
					wantRequests = 1
				}
				if err != nil || result == nil || !result.Settled || requests != wantRequests {
					t.Fatal("owned provider inventory did not settle", err, requests)
				}
				assertRichDeadlineAndLease(t, f, "dom")
				var inserted, failures, missing int
				var publisherReserved bool
				if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid) FROM job_board WHERE id=$1::uuid", f.board, f.original).Scan(&failures, &publisherReserved, &inserted); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
					t.Fatal(err)
				}
				if reserved || c.Status != "complete" {
					wantFailures := 1
					if reserved {
						wantFailures = 0
					}
					if inserted != 0 || missing != 0 || failures != wantFailures || publisherReserved != reserved {
						t.Fatal("unproved inventory or reserved publisher changed canonical authority")
					}
					return
				}
				wantMissing := 0
				if len(c.Expected) > 0 {
					wantMissing = 1
				}
				if inserted != len(c.Expected) || failures != 0 || publisherReserved || missing != wantMissing {
					t.Fatal("original complete or first-empty inventory changed settlement", inserted, failures, missing)
				}
				for _, job := range c.Expected {
					var title string
					if err := f.pg.QueryRow(ctx, "SELECT coalesce(titles[1],'') FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, job["url"]).Scan(&title); err != nil {
						t.Fatal("original canonical identity absent", err)
					}
					if want, ok := job["title"].(string); ok && title != want {
						t.Fatal("original title changed during persistence")
					}
				}
			})
		}
	}
}
