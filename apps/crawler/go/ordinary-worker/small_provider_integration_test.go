package worker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealSmallProvidersCanonicalFieldsAndFailedInventorySettlement(t *testing.T) {
	for _, c := range smallProviderOracleCases(t) {
		if c.Name != "populated" {
			continue
		}
		for _, mode := range []string{"complete", "partial", "reserved", "gone"} {
			t.Run(c.Provider+"/"+mode, func(t *testing.T) {
				f := privateRichPipelineFixtureURL(t, c.Provider, `{"scraper_type":"skip"}`, c.Board.URL)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "gone" {
						w.WriteHeader(404)
						return
					}
					body := c.Pages[0]
					if mode == "partial" {
						var value map[string]any
						if json.Unmarshal(body, &value) != nil {
							t.Fatal("fixture")
						}
						switch c.Provider {
						case "cnstaff":
							value["total"] = 2
						case "jobbank104":
							value["data"].(map[string]any)["totalCount"] = 2
						case "seamlesshiring":
							value["data"].(map[string]any)["jobs"].(map[string]any)["total"] = 2
						}
						body, _ = json.Marshal(value)
					}
					w.Header().Set("Content-Type", "application/json")
					w.Write(body)
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("real claim did not settle", e, result)
				}
				assertRichDeadlineAndLease(t, f, c.Provider)
				var reserved bool
				var failures, gone int
				if e = f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				wantFailures, wantGone := 0, 0
				if mode == "partial" && c.Provider != "seamlesshiring" {
					wantFailures = 1
				}
				if mode == "gone" {
					if c.Provider == "seamlesshiring" {
						wantFailures = 1
					} else {
						wantGone = 1
					}
				}
				if reserved != (mode == "reserved") || failures != wantFailures || gone != wantGone {
					t.Fatal("terminal board state differs", reserved, failures, gone)
				}
				if mode != "complete" {
					var active bool
					var title string
					if e = f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil || !active || title != "Original" {
						t.Fatal("unproved inventory changed original posting", e, active, title)
					}
					if mode != "partial" || c.Provider != "seamlesshiring" {
						if result.Batches.Inserted != 0 {
							t.Fatal("failed inventory published a prefix")
						}
						return
					}
				}
				if result.Batches.Inserted != 1 {
					t.Fatal("rich posting omitted", result.Batches)
				}
				expected, e := secondaryRichJob(c.Expected.Jobs[0])
				if e != nil || expected.Description == nil {
					t.Fatal("expected canonical description missing", e)
				}
				var count int
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND d.html=$3 AND NOT d.r2_uploaded AND p.next_scrape_at IS NULL", f.board, c.Expected.Jobs[0]["url"], *expected.Description).Scan(&count); e != nil || count != 1 {
					t.Fatal("canonical HTML, R2 intent or skip detail scheduling differs", e, count)
				}
			})
		}
	}
}
func TestRealJobbankRequiredProxyRejectsDirectClient(t *testing.T) {
	f := privateRichPipelineFixtureURL(t, "jobbank104", `{"proxy":true,"scraper_type":"skip"}`, "https://www.104.com.tw/company/abcde")
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	calls := 0
	direct := verifiedClaimFixtureClient(t, http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls++ }))
	result, e := RunGreenhouseClaim(ctx, f.a, claim, direct, richPipelinePreparer(t, f), circuits)
	if !errors.Is(e, queue.ErrUnsupportedProfile) || result != nil && result.Settled || calls != 0 {
		t.Fatal("required proxy acquired direct execution", e, result, calls)
	}
}

func TestRealJobbankRequiredProxyCanonicalFields(t *testing.T) {
	var oracle smallProviderOracle
	for _, c := range smallProviderOracleCases(t) {
		if c.Provider == "jobbank104" && c.Name == "populated" {
			oracle = c
			break
		}
	}
	f := privateRichPipelineFixtureURL(t, "jobbank104", `{"proxy":true,"scraper_type":"skip"}`, oracle.Board.URL)
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	calls := 0
	origin := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Content-Type", "application/json")
		w.Write(oracle.Pages[0])
	}))
	proxy := credentialedProxyFixture(t, origin)
	result, e := RunGreenhouseClaim(ctx, f.a, claim, proxy, richPipelinePreparer(t, f), circuits)
	if e != nil || result == nil || !result.Settled || result.Batches.Inserted != 1 || calls != 1 {
		t.Fatal("sealed proxy did not publish exact rich inventory", e, result, calls)
	}
	assertRichDeadlineAndLease(t, f, "jobbank104")
	var count int
	if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND NOT d.r2_uploaded AND p.next_scrape_at IS NULL", f.board, oracle.Expected.Jobs[0]["url"]).Scan(&count); e != nil || count != 1 {
		t.Fatal("proxy canonical description/R2 publication omitted", e, count)
	}
}
