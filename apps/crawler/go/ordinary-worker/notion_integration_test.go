package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealNotionCompleteInventoryFailuresAndPublisherSettlement(t *testing.T) {
	cases := notionHTTPCases(t)
	for _, mode := range []string{"complete", "failed", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			c := cases[0]
			if mode == "failed" {
				c = cases[12]
			}
			var metadata map[string]any
			if json.Unmarshal(c.Metadata, &metadata) != nil {
				t.Fatal("metadata")
			}
			metadata["scraper_type"] = "notion"
			body, _ := json.Marshal(metadata)
			f := privateRichPipelineFixtureURL(t, "notion", string(body), c.Board)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, notionHTTPHandler(t, c, mode == "reserved"))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			assertRichDeadlineAndLease(t, f, "notion")
			var failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if result.Batches.Inserted != 1 || failures != 0 || reserved {
					t.Fatal(result.Batches, failures, reserved)
				}
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL", f.board, f.original).Scan(&count); err != nil || count != 1 {
					t.Fatal("detail intent lost", count, err)
				}
				return
			}
			var active bool
			var title string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil {
				t.Fatal(err)
			}
			if !active || title != "Original" || result.Batches.Inserted != 0 || reserved != (mode == "reserved") || mode == "failed" && failures != 1 {
				t.Fatal("failed prefix changed canonical inventory", active, title, failures, reserved, result.Batches)
			}
		})
	}
}

func notionDetailOwnedFixture(t *testing.T) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	t.Helper()
	f := privateRichPipelineFixtureURL(t, "notion", `{"scraper_type":"notion"}`, "https://fixture.notion.site/")
	ctx := context.Background()
	var epoch int64
	if err := f.pg.QueryRow(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active' RETURNING routing_epoch").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active", "monitors_simple:notion", "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	source := "https://fixture.notion.site/11111111111111111111111111111111"
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.original, source); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: f.board, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
		t.Fatal(err)
	}
	a, err := queue.OpenAuthority(ctx, f.dsn, f.client, epoch)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	plan, err := a.StageOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board}, []string{f.board})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", plan.SHA256()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	if err := f.r.Set(ctx, "ordinary:ownership:active", plan.ProjectionJSON(), 0).Err(); err != nil {
		t.Fatal(err)
	}
	owned, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, epoch, plan.SHA256(), plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(owned.Close)
	claim, err := owned.Claim(ctx, queue.Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != f.original || claim.Descriptor().Kind != queue.Scrape {
		t.Fatal("canonical Notion detail claim missing", err, claim)
	}
	return f, owned, claim
}

func TestRealNotionDetailFieldsDescriptionsAndFailedContentPreservation(t *testing.T) {
	var corpus []struct{ Data json.RawMessage }
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_notion_detail.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"complete", "missing_block", "reserved", "malformed", "503"} {
		t.Run(mode, func(t *testing.T) {
			f, owned, claim := notionDetailOwnedFixture(t)
			ctx := context.Background()
			calls := 0
			body := append([]byte(nil), corpus[0].Data...)
			if mode == "missing_block" {
				var d map[string]any
				json.Unmarshal(body, &d)
				delete(d["recordMap"].(map[string]any)["block"].(map[string]any), "e")
				body, _ = json.Marshal(d)
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "POST" || r.Host != "fixture.notion.site" || r.URL.Path != "/api/v3/loadPageChunk" {
					t.Error("unbound Notion detail request")
				}
				w.Header().Set("Content-Type", "application/json")
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
				}
				if mode == "503" {
					w.WriteHeader(503)
					fmt.Fprint(w, `{}`)
					return
				}
				if mode == "malformed" {
					fmt.Fprint(w, `{`)
					return
				}
				w.Write(body)
			}))
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			status := "failed"
			if mode == "complete" {
				status = "succeeded"
			}
			if mode == "reserved" {
				status = "publisher_reserved"
			}
			if result.Cycle.Status != status {
				t.Fatal(status, result.Cycle.Status)
			}
			var title string
			if err := f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if title != "Engineer & Operations" {
					t.Fatal(title)
				}
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid AND html LIKE '%Build &lt;Go&gt;%' AND NOT r2_uploaded", f.original).Scan(&count); err != nil || count != 1 {
					t.Fatal("canonical description/R2 intent lost", count, err)
				}
			} else if title != "Original" {
				t.Fatal("failed detail overwrote canonical content", title)
			}
			if (mode == "malformed" || mode == "503") && calls != 3 {
				t.Fatal("retry boundary differs", calls)
			}
		})
	}
}
