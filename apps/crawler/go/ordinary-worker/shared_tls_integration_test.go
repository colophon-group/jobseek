package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"testing"
)

func TestRealSharedExplicitHTTPMonitorTLSExceptionCanonicalTerminalEffects(t *testing.T) {
	for _, provider := range []string{"dom", "inline", "sitemap"} {
		for _, reserved := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/%t", provider, reserved), func(t *testing.T) {
				md := map[string]any{"skip_ssl": true, "scraper_type": "json-ld"}
				if provider == "dom" {
					md["url_filter"] = "/jobs/"
				}
				if provider == "sitemap" {
					md["sitemap_url"] = "https://example.com/jobs.xml"
				}
				if provider == "inline" {
					md["scraper_type"] = "skip"
					md["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "p", "field": "description", "html": true}}
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, provider, string(raw))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := explicitTLSFixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					if reserved {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "tls-fixture-policy")
						w.WriteHeader(503)
						return
					}
					if provider == "sitemap" {
						fmt.Fprint(w, `<urlset><url><loc>https://example.com/jobs/engineer</loc></url></urlset>`)
					} else if provider == "dom" {
						fmt.Fprint(w, `<a href="/jobs/engineer">Engineer</a>`)
					} else {
						fmt.Fprint(w, `<h1>Engineer</h1><p>Build services</p>`)
					}
				}, true, provider == "sitemap")
				wrong := *client
				wrong.skipSSL = false
				if result, e := RunGreenhouseClaim(ctx, f.a, claim, &wrong, richPipelinePreparer(t, f), circuits); e == nil || result.Settled || calls != 0 {
					t.Fatal("unmatched sealed transport reached effects")
				}
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled || calls != 1 {
					t.Fatal("explicit TLS inventory did not settle", e, calls)
				}
				var originalActive, boardReserved bool
				var count, missing, failures int
				if e := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&originalActive, &missing); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&boardReserved, &failures, &count); e != nil {
					t.Fatal(e)
				}
				if reserved {
					if !originalActive || missing != 0 || !boardReserved || failures != 0 || count != 1 || result.Cycle.Status != "publisher_reserved" {
						t.Fatal("reservation lost precedence or changed inventory")
					}
				} else {
					if missing != 1 || boardReserved || failures != 0 || count != 2 || result.Batches.Inserted != 1 {
						t.Fatal("complete original inventory not preserved")
					}
					if provider != "inline" && f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val()+f.r.ZCard(ctx, "scrapes_simple:example.com").Val() != 1 {
						t.Fatal("monitor TLS changed independent detail work")
					}
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}

func TestRealNativeExecutableSelectsExplicitHTTPMonitorTLSException(t *testing.T) {
	for _, provider := range []string{"dom", "inline", "sitemap"} {
		t.Run(provider, func(t *testing.T) {
			md := map[string]any{"skip_ssl": true, "scraper_type": "json-ld"}
			if provider == "sitemap" {
				md["sitemap_url"] = "https://example.com/jobs.xml"
			}
			if provider == "inline" {
				md["scraper_type"] = "skip"
				md["steps"] = []any{map[string]any{"tag": "h1", "field": "title"}}
			}
			raw, _ := json.Marshal(md)
			f := privateRichPipelineFixture(t, provider, string(raw))
			ctx := context.Background()
			if _, e := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); e != nil {
				t.Fatal(e)
			}
			executable := newNativeExecutableFixture(t, f, privatePipelineReferenceDSN(t, f))
			process := executable.start(t, "explicit-monitor-tls")
			waitNativeFixture(t, process, "immutable TLS client selection and no-fetch publisher settlement", func() bool {
				var state string
				e := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state)
				return e == nil && state == "completed" && f.r.ZCard(ctx, "inflight:simple").Val() == 0
			})
			if e := process.command.Process.Signal(os.Interrupt); e != nil {
				t.Fatal(e)
			}
			if e := process.wait(t); e != nil {
				t.Fatal("installed runtime did not drain", e)
			}
			if skip, e := queue.MonitorSkipsSSL(map[string]string{"crawler_type": provider, "monitor_needs_browser": "0", "metadata": string(raw)}); e != nil || !skip {
				t.Fatal("fixture lost original selector")
			}
		})
	}
}
