package worker

import (
	"context"
	"encoding/json"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"
)

func seekDetailOwnedFixture(t *testing.T) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	t.Helper()
	f := privateRichPipelineFixtureURL(t, "seek", `{"scraper_type":"seek"}`, "https://au.seek.com/jobs?advertiserid=9094357")
	ctx := context.Background()
	var epoch int64
	if err := f.pg.QueryRow(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active' RETURNING routing_epoch").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active", "monitors_simple:seek", "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	source := "https://au.seek.com/job/94267983"
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
		t.Fatal("canonical SEEK detail claim missing", err, claim)
	}
	return f, owned, claim
}

func TestRealSeekDetailCanonicalFieldsFailuresAndPublisherSettlement(t *testing.T) {
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_seek_detail.json")
	var cases []struct{ Payload json.RawMessage }
	if err != nil || json.Unmarshal(raw, &cases) != nil {
		t.Fatal(err)
	}
	for _, mode := range []string{"complete", "wrong_job", "wrong_advertiser", "expired", "503_reserved", "body_reserved", "malformed"} {
		t.Run(mode, func(t *testing.T) {
			f, owned, claim := seekDetailOwnedFixture(t)
			calls := 0
			body := string(cases[0].Payload)
			if mode == "wrong_job" {
				body = strings.ReplaceAll(body, "94267983", "999")
			}
			if mode == "wrong_advertiser" {
				body = strings.ReplaceAll(body, "9094357", "999")
			}
			if mode == "expired" {
				body = strings.ReplaceAll(body, `"isExpired": false`, `"isExpired": true`)
			}
			if mode == "malformed" {
				body = "{"
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Host != "au.seek.com" || r.URL.Path != "/graphql" || r.Method != "POST" {
					t.Error("unbound SEEK API request")
				}
				w.Header().Set("Content-Type", "application/json")
				if mode == "503_reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
				}
				if mode == "body_reserved" {
					w.Header().Set("Content-Type", "text/html")
					body = `<meta name="tdm-reservation" content="1">`
				}
				w.Write([]byte(body))
			}))
			ctx := context.Background()
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, owned, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			want := "failed"
			if mode == "complete" {
				want = "succeeded"
			}
			if strings.Contains(mode, "reserved") {
				want = "publisher_reserved"
			}
			if result.Cycle.Status != want {
				t.Fatal(result.Cycle.Status, want)
			}
			var title string
			if err := f.pg.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if title != "Forklift Operator" {
					t.Fatal(title)
				}
				var n int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions WHERE posting_id=$1::uuid AND html LIKE '%Operate equipment safely.%' AND NOT r2_uploaded", f.original).Scan(&n); err != nil || n != 1 {
					t.Fatal(n, err)
				}
			} else if title != "Original" {
				t.Fatal("failed detail rewrote title", title)
			}
			if strings.Contains(mode, "reserved") && calls != 1 {
				t.Fatal("reservation retried", calls)
			}
			if mode == "malformed" && calls != 3 {
				t.Fatal("retry contract", calls)
			}
		})
	}
}
