package worker

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func jsonldOwnedFixture(t *testing.T) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	return independentDetailOwnedFixture(t, `{"scraper_type":"json-ld","selector":"a.job","render":true,"scraper_config":{"defaults":{"language":"en"}}}`, "")
}

func independentDetailOwnedFixture(t *testing.T, metadata, source string, workers ...queue.WorkerType) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	t.Helper()
	worker := queue.Simple
	if len(workers) == 1 {
		worker = workers[0]
	}
	f := privatePipelineFixture(t)
	ctx := context.Background()
	var epoch int64
	if err := f.pg.QueryRow(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE state='active' RETURNING routing_epoch").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active", "monitors_simple:greenhouse", "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	board := fixtureID(t)
	if _, err := f.pg.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,'https://careers.example.net/jobs','dom',$4::jsonb,60,24,'careers.example.net',true,$5)`, board, f.company, "jsonld-"+board, metadata, worker == queue.Browser); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = f.pg.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board) })
	config := map[string]string{"board_slug": "jsonld-" + board, "company_id": f.company, "board_url": "https://careers.example.net/jobs", "crawler_type": "dom", "metadata": metadata, "domain": "careers.example.net", "throttle_key": "careers.example.net", "monitor_needs_browser": "1", "scraper_needs_browser": "0", "check_interval_minutes": "60", "scrape_interval_hours": "24"}
	if worker == queue.Browser {
		config["scraper_needs_browser"] = "1"
	}
	if err := f.r.HSet(ctx, "board:"+board, config).Err(); err != nil {
		t.Fatal(err)
	}
	if source == "" {
		source = "https://example.com/job/" + f.original
	}
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid,source_url=$3,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.original, board, source); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: board, URL: source, Due: time.Now().Add(-time.Minute), Browser: worker == queue.Browser}); err != nil {
		t.Fatal(err)
	}
	a, err := queue.OpenAuthority(ctx, f.dsn, f.client, epoch)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	plan, err := a.StageOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board}, []string{board})
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
	claim, err := owned.Claim(ctx, worker)
	if err != nil || claim == nil || claim.Descriptor().ID != f.original || claim.Descriptor().Kind != queue.Scrape {
		t.Fatal("native JSON-LD claim missing", err)
	}
	return f, owned, claim
}

const nativeJSONLDHTML = `<html><head><script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting","title":"Senior Software Engineer","description":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","employmentType":"FULL_TIME","jobLocation":{"@type":"Place","address":{"addressLocality":"Zurich"}}}</script></head></html>`

func TestRealJSONLDDetailUsesVerifiedHTTPSharedEnrichmentAndCanonicalSettlement(t *testing.T) {
	f, a, claim := jsonldOwnedFixture(t)
	ctx := context.Background()
	calls := 0
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "GET" || r.URL.Path != "/job/"+f.original {
			t.Error("wrong canonical request")
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, nativeJSONLDHTML)
	})
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 || result.HTTP.Requests != 1 || result.HTTP.Responses != 1 || result.TaskKind != queue.Scrape {
		t.Fatal("native JSON-LD execution failed", result, err)
	}
	var title, html, employment, currency string
	var locations []int32
	var due time.Time
	var uploaded bool
	var canonicalHash *int64
	var pendingHash int64
	if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],d.html,p.employment_type,p.salary_currency,p.location_ids,p.next_scrape_at,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &html, &employment, &currency, &locations, &due, &uploaded, &canonicalHash, &pendingHash); err != nil {
		t.Fatal(err)
	}
	if title != "Senior Software Engineer" || !strings.Contains(html, "Salary CHF") || employment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || uploaded || canonicalHash != nil || pendingHash == 0 {
		t.Fatal("JSON-LD canonical fields/pending description differ")
	}
	score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("native deadline/lease differs", err)
	}
}

func TestRealJSONLDDetailPreservesOptOutFailureAndFreshCanonicalPolicy(t *testing.T) {
	for _, mode := range []string{"header-reserved", "meta-reserved", "inactive-header-reserved", "existing-reserved", "fresh-reserved", "404-gone", "503-transient", "empty", "transport"} {
		t.Run(mode, func(t *testing.T) {
			f, a, claim := jsonldOwnedFixture(t)
			ctx := context.Background()
			calls := 0
			if mode == "existing-reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				status, body := 200, nativeJSONLDHTML
				switch mode {
				case "header-reserved", "inactive-header-reserved":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://example.com/policy")
					status = 410
					if mode == "inactive-header-reserved" {
						if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
							t.Fatal(err)
						}
					}
				case "meta-reserved":
					body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">`
				case "fresh-reserved":
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				case "404-gone":
					status = 404
				case "503-transient":
					status = 503
				case "empty":
					body = "<html>no JobPosting</html>"
				}
				w.WriteHeader(status)
				fmt.Fprint(w, body)
			})
			if mode == "transport" {
				client = &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) { calls++; return nil, io.ErrUnexpectedEOF })}}
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("JSON-LD failure/policy did not settle", err)
			}
			var title string
			var active, reserved bool
			var descriptions int
			var due *time.Time
			if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); err != nil {
				t.Fatal(err)
			}
			if title != "Original" || descriptions != 0 {
				t.Fatal("non-success rewrote content")
			}
			wantActive := mode != "404-gone" && mode != "inactive-header-reserved"
			if active != wantActive {
				t.Fatal("JSON-LD changed visibility incorrectly", mode)
			}
			if strings.Contains(mode, "reserved") && !reserved {
				t.Fatal("positive publisher reservation lost", mode)
			}
			if mode == "existing-reserved" && calls != 0 {
				t.Fatal("reserved job fetched")
			}
			if mode == "empty" && calls != 2 {
				t.Fatal("JSON-LD empty-content retry changed")
			}
			if mode == "inactive-header-reserved" && (due != nil || result.Cycle.Status != "unscheduled") {
				t.Fatal("inactive opt-out rescheduled")
			}
			if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("failed JSON-LD attempt retained lease")
			}
		})
	}
}
