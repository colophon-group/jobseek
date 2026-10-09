package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"testing"
)

func TestRealGroupedHTTPProvidersCanonicalAndQueueSettlement(t *testing.T) {
	for _, c := range groupedHTTPInventoryCases(t) {
		if c.Mode != "rich" && c.Mode != "empty" && c.Mode != "paged" && c.Mode != "duplicate" && c.Mode != "mixed-invalid" && c.Mode != "semantic-zero" && c.Mode != "snapshot-change" {
			continue
		}
		modes := []string{"complete"}
		if c.Error {
			modes = []string{"original-failure"}
		}
		if c.Mode == "rich" {
			modes = append(modes, "invalid", "reserved", "gone")
		}
		if c.Provider == "jobconvo" && c.Mode == "rich" {
			modes = append(modes, "first-party-redirect")
		}
		for _, mode := range modes {
			t.Run(c.Provider+"/"+c.Mode+"/"+mode, func(t *testing.T) {
				var md map[string]any
				json.Unmarshal(c.Board.Metadata, &md)
				md["scraper_type"] = "skip"
				if c.Provider == "inploi" {
					md["scraper_type"] = "json-ld"
					md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				if c.Provider == "jobconvo" {
					md["scraper_type"] = "jobconvo"
					md["scraper_config"] = map[string]any{"locale": "pt-br"}
				}
				metadata, _ := json.Marshal(md)
				f := privateRichPipelineFixtureURL(t, c.Provider, string(metadata), c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				used := make([]bool, len(c.Exchanges))
				var mu sync.Mutex
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "first-party-redirect" {
						if r.Host == "jobs.jobconvo.com" {
							http.Redirect(w, r, "https://app.jobconvo.com:443"+r.URL.RequestURI(), 301)
							return
						}
						fmt.Fprint(w, c.Exchanges[0].Body)
						return
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "gone" {
						w.WriteHeader(404)
						return
					}
					if mode == "invalid" {
						fmt.Fprint(w, "malformed")
						return
					}
					body, _ := io.ReadAll(io.LimitReader(r.Body, 1<<20))
					source := "https://" + r.Host + r.URL.RequestURI()
					mu.Lock()
					defer mu.Unlock()
					for i, x := range c.Exchanges {
						if !used[i] && x.URL == source && x.Method == r.Method && groupedHTTPRequestBodyEqual(x.Headers["content-type"], string(body), x.RequestBody) {
							used[i] = true
							w.Header().Set("Content-Type", "application/json")
							if c.Provider == "jobconvo" {
								w.Header().Set("Content-Type", "text/html")
							}
							w.WriteHeader(x.Status)
							fmt.Fprint(w, x.Body)
							return
						}
					}
					t.Error("unmatched original owned exchange")
					w.WriteHeader(400)
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("owned inventory failed to settle", e, result)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var reserved bool
				var failures, gone int
				if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				wantFailure, wantGone := 0, 0
				if mode == "invalid" || mode == "original-failure" || mode == "gone" && c.Provider != "jobconvo" {
					wantFailure = 1
				}
				if mode == "gone" && c.Provider == "jobconvo" {
					wantGone = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailure || gone != wantGone {
					t.Fatal("board failure/gone/policy state changed", reserved, failures, gone)
				}
				if mode != "complete" && mode != "first-party-redirect" {
					var active bool
					var title string
					if result.Batches.Inserted != 0 {
						t.Fatal("failed inventory yielded prefix")
					}
					if e = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil || !active || title != "Original" {
						t.Fatal("failed inventory changed old posting", e)
					}
					return
				}
				var expected []map[string]any
				if c.Provider == "jobconvo" {
					var urls []string
					json.Unmarshal(c.Jobs, &urls)
					for _, source := range urls {
						expected = append(expected, map[string]any{"url": source})
					}
				} else {
					json.Unmarshal(c.Jobs, &expected)
				}
				if result.Batches.Inserted != len(expected) {
					t.Fatal("canonical insertion/truncation changed", result.Batches)
				}
				if c.Truncated {
					var active bool
					if e = f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); e != nil || !active {
						t.Fatal("partial inventory delisted unseen job", e)
					}
				}
				for _, fields := range expected {
					source := fields["url"].(string)
					var identity string
					var scheduled bool
					var descriptions int
					if e = f.pg.QueryRow(ctx, `SELECT source_identity,next_scrape_at IS NOT NULL,(SELECT count(*) FROM descriptions d WHERE d.posting_id=p.id) FROM job_posting p WHERE board_id=$1::uuid AND source_url=$2`, f.board, source).Scan(&identity, &scheduled, &descriptions); e != nil {
						t.Fatal(e)
					}
					if identity != source || scheduled != (c.Provider != "curately") || c.Provider == "curately" && descriptions != 1 {
						t.Fatal("original URL identity/description/scrape schedule changed", identity, scheduled, descriptions)
					}
					if c.Provider != "jobconvo" {
						var title string
						if e = f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&title); e != nil || strings.TrimSpace(fmt.Sprint(fields["title"])) != title {
							t.Fatal("canonical title changed", e, title)
						}
					}
				}
			})
		}
	}
}
