package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

const jobStreetFixtureBoard = "https://my.jobstreet.com/companies/tecan-cdmo-solutions-pn-175608148114568/jobs"
const jobStreetFixtureMetadata = `{"host":"my.jobstreet.com","company_id":"175608148114568","organisation_id":"744981","scraper_type":"jobstreet","scraper_config":{"enrich":["title","description","locations","employment_type","date_posted","base_salary"]}}`

func TestRealJobStreetAndLegacySessionCanonicalSettlement(t *testing.T) {
	var jobCases []struct {
		Name, Kind, Host string
		Page             json.RawMessage
	}
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jobstreet.json")
	if e != nil || json.Unmarshal(raw, &jobCases) != nil {
		t.Fatal(e)
	}
	jobPage := []byte{}
	for _, c := range jobCases {
		if c.Name == "my.jobstreet.com-complete" {
			jobPage = c.Page
		}
	}
	var legacy struct {
		Inventories []struct {
			Name      string
			Responses []struct {
				Body    string
				Headers map[string]string
			}
		}
	}
	raw, e = os.ReadFile("../api-sniffer-monitor/testdata/python_successfactors_legacy.json")
	if e != nil || json.Unmarshal(raw, &legacy) != nil {
		t.Fatal(e)
	}
	for _, provider := range []string{"jobstreet", "rss"} {
		for _, mode := range []string{"complete", "invalid", "reserved", "prefix-failure"} {
			if provider == "jobstreet" && mode == "prefix-failure" {
				continue
			}
			t.Run(provider+"/"+mode, func(t *testing.T) {
				board, metadata := jobStreetFixtureBoard, jobStreetFixtureMetadata
				if provider == "rss" {
					board = "https://career5.successfactors.eu/career?company=Acme"
					metadata = `{"preset":"successfactors","variant":"legacy","host":"career5.successfactors.eu","company":"Acme","scraper_type":"dom","scraper_config":{"scope":".joqReqDescription","steps":[{"field":"description","html":true}],"enrich":["description"]}}`
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, board)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					index := calls
					calls++
					if mode == "reserved" && (provider == "jobstreet" || index == 2) {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "invalid" {
						fmt.Fprint(w, `{"invalid":true}`)
						return
					}
					if provider == "jobstreet" {
						w.Header().Set("Content-Type", "application/json")
						_, _ = w.Write(jobPage)
						return
					}
					name := "one"
					if mode == "prefix-failure" {
						name = "short-later-page"
					}
					for _, c := range legacy.Inventories {
						if c.Name == name {
							if index >= len(c.Responses) {
								t.Error("unexpected session request")
								w.WriteHeader(500)
								return
							}
							for k, v := range c.Responses[index].Headers {
								if !strings.EqualFold(k, "Content-Length") {
									w.Header().Set(k, v)
								}
							}
							fmt.Fprint(w, c.Responses[index].Body)
							return
						}
					}
					t.Error("original session fixture missing")
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal(result, e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var reserved bool
				var failures, missing int
				if e = f.pg.QueryRow(ctx, "SELECT b.tdm_reserved,b.consecutive_failures,p.missing_count FROM job_board b JOIN job_posting p ON p.board_id=b.id WHERE p.id=$1::uuid", f.original).Scan(&reserved, &failures, &missing); e != nil {
					t.Fatal(e)
				}
				if mode == "complete" || mode == "prefix-failure" {
					wantCount := 1
					if mode == "prefix-failure" {
						wantCount = 100
					}
					if result.Batches.Inserted != wantCount {
						t.Fatal("original committed batch count", result.Batches)
					}
					var due *time.Time
					var id, title string
					query := "SELECT id::text,titles[1],next_scrape_at FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid ORDER BY source_url LIMIT 1"
					if e = f.pg.QueryRow(ctx, query, f.board, f.original).Scan(&id, &title, &due); e != nil || due == nil {
						t.Fatal("detail schedule missing", e, due)
					}
					if provider == "jobstreet" && title != "Role 12345678" || provider == "rss" && !strings.HasPrefix(title, "Engineer ") {
						t.Fatal("canonical title", title)
					}
					if f.r.HGet(ctx, "scrape:"+id, "board_id").Val() != f.board {
						t.Fatal("detail queue metadata missing")
					}
					wantMissing := 1
					if mode == "prefix-failure" {
						wantMissing = 0
					}
					if missing != wantMissing || reserved || failures != map[bool]int{true: 1, false: 0}[mode == "prefix-failure"] {
						t.Fatal("absence/failure conservation", missing, failures, reserved)
					}
				} else if result.Batches.Inserted != 0 || missing != 0 || reserved != (mode == "reserved") || failures != map[bool]int{true: 1, false: 0}[mode == "invalid"] {
					t.Fatal("failure/policy conservation", result.Batches, missing, failures, reserved)
				}
			})
		}
	}
}
func jobStreetDetailOwnedFixture(t *testing.T) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	f := privateRichPipelineFixtureURL(t, "jobstreet", jobStreetFixtureMetadata, jobStreetFixtureBoard)
	ctx := context.Background()
	var epoch int64
	if e := f.pg.QueryRow(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active' RETURNING routing_epoch").Scan(&epoch); e != nil {
		t.Fatal(e)
	}
	if e := f.r.Del(ctx, "ordinary:ownership:active", "monitors_simple:jobstreet", "ready:simple:1").Err(); e != nil {
		t.Fatal(e)
	}
	source := "https://my.jobstreet.com/job/12345678"
	if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.original, source); e != nil {
		t.Fatal(e)
	}
	if _, e := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: f.board, URL: source, Due: time.Now().Add(-time.Minute)}); e != nil {
		t.Fatal(e)
	}
	a, e := queue.OpenAuthority(ctx, f.dsn, f.client, epoch)
	if e != nil {
		t.Fatal(e)
	}
	defer a.Close()
	plan, e := a.StageOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board}, []string{f.board})
	if e != nil {
		t.Fatal(e)
	}
	if _, e := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", plan.SHA256()); e != nil {
		t.Fatal(e)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	if e := f.r.Set(ctx, "ordinary:ownership:active", plan.ProjectionJSON(), 0).Err(); e != nil {
		t.Fatal(e)
	}
	owned, e := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, epoch, plan.SHA256(), plan.SourceRevision())
	if e != nil {
		t.Fatal(e)
	}
	t.Cleanup(owned.Close)
	claim, e := owned.Claim(ctx, queue.Simple)
	if e != nil || claim == nil || claim.Descriptor().ID != f.original || claim.Descriptor().Kind != queue.Scrape {
		t.Fatal("detail ownership", e, claim)
	}
	return f, owned, claim
}
func TestRealJobStreetDetailCanonicalFailuresAndReservation(t *testing.T) {
	var cases []struct {
		Name string
		Page json.RawMessage
	}
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jobstreet.json")
	if e != nil || json.Unmarshal(raw, &cases) != nil {
		t.Fatal(e)
	}
	body := ""
	for _, c := range cases {
		if c.Name == "my.jobstreet.com-detail-complete" {
			body = string(c.Page)
		}
	}
	if body == "" {
		t.Fatal("original detail fixture")
	}
	for _, mode := range []string{"complete", "expired", "wrong-id", "malformed", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			f, owned, claim := jobStreetDetailOwnedFixture(t)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				content := body
				if mode == "expired" {
					content = strings.ReplaceAll(content, `"isExpired": false`, `"isExpired": true`)
				}
				if mode == "wrong-id" {
					content = strings.ReplaceAll(content, "12345678", "999")
				}
				if mode == "malformed" {
					content = "{"
				}
				w.Header().Set("Content-Type", "application/json")
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
				}
				fmt.Fprint(w, content)
			}))
			ctx := context.Background()
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal(result, e)
			}
			want := "failed"
			if mode == "complete" {
				want = "succeeded"
			}
			if mode == "reserved" {
				want = "publisher_reserved"
			}
			if result.Cycle.Status != want {
				t.Fatal(result.Cycle.Status, want)
			}
			var title string
			var n int
			var due *time.Time
			if e := f.pg.QueryRow(ctx, "SELECT titles[1],next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id AND NOT r2_uploaded) FROM job_posting p WHERE id=$1::uuid", f.original).Scan(&title, &due, &n); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if title != "Role 12345678" || n != 1 {
					t.Fatal("detail canonical content", title, n)
				}
			} else if title != "Original" || n != 0 {
				t.Fatal("failed detail rewrote content", title, n)
			}
			if due == nil || calls != 1 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
				t.Fatal("detail schedule/ACK", due, calls)
			}
		})
	}
}
