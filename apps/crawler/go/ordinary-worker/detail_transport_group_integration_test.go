package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealGroupedDetailTransportCanonicalWritesAndConservation(t *testing.T) {
	for _, scraper := range []string{"workday", "dom", "json-ld"} {
		for _, mode := range []string{"success", "reserved", "transient"} {
			t.Run(scraper+"/"+mode, func(t *testing.T) {
				var f nativePipelineFixture
				var authority *queue.Authority
				var claim *queue.Claim
				if scraper == "workday" {
					f, authority, claim, _ = workdayDetailFixture(t, true, `{"all_sites":false,"scraper_type":"workday","scraper_config":{"proxy":true}}`)
				} else {
					metadata := `{"scraper_type":"json-ld","scraper_config":{"skip_ssl":true}}`
					if scraper == "dom" {
						metadata = `{"scraper_type":"dom","scraper_config":{"skip_ssl":true,"steps":[{"tag":"h1","field":"title"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}]}}`
					}
					f, authority, claim = independentDetailOwnedFixture(t, metadata, "")
				}
				ctx := context.Background()
				calls := 0
				handler := func(w http.ResponseWriter, r *http.Request) {
					calls++
					if r.Header.Get("Proxy-Authorization") != "" {
						t.Error("proxy credential reached origin")
					}
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
						return
					}
					if mode == "transient" {
						w.WriteHeader(503)
						return
					}
					body := nativeJSONLDHTML
					if scraper == "dom" {
						body = nativeDOMHTML
					} else if scraper == "workday" {
						if r.Host != "fixture.wd1.myworkdayjobs.com" || r.URL.Path != "/wday/cxs/fixture/Careers/job/City/Engineer_R100" {
							t.Error("Workday proxy changed canonical CXS request")
						}
						body = `{"jobPostingInfo":{"title":"Senior Software Engineer","jobDescription":"<p>Build reliable services.</p>","location":"Zurich"}}`
					}
					fmt.Fprint(w, body)
				}
				var client *VerifiedHTTP
				if scraper == "workday" {
					client = credentialedProxyFixture(t, richPipelineHTTP(t, handler))
				} else {
					// Actual TLS with an unrelated trust root; only the explicit
					// canonical exception allows this origin certificate.
					client = explicitTLSFixtureClient(t, handler, true, true)
				}
				proxy, err := runtimeClaimUsesProxy(ctx, authority, claim)
				if err != nil || proxy != (scraper == "workday") {
					t.Fatal("canonical proxy transport selection lost", err)
				}
				skip, err := runtimeClaimSkipsSSL(ctx, authority, claim)
				if err != nil || skip != (scraper != "workday") {
					t.Fatal("canonical TLS transport selection lost", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				wrong := *client
				if proxy {
					wrong.proxyRequired = false
				} else {
					wrong.skipSSL = false
				}
				processor := richPipelinePreparer(t, f).Processor
				if result, err := RunDetail(ctx, authority, claim, &wrong, processor, circuits); err == nil || result.Settled || calls != 0 {
					t.Fatal("wrong sealed client reached an effect")
				}
				result, err := RunDetail(ctx, authority, claim, client, processor, circuits)
				if err != nil || !result.Settled || calls == 0 {
					t.Fatal("grouped detail transport did not settle", err, calls)
				}
				var title string
				var due time.Time
				var lease *time.Time
				var reserved bool
				var descriptions int
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],next_scrape_at,leased_until,tdm_reserved,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &due, &lease, &reserved, &descriptions); err != nil {
					t.Fatal(err)
				}
				if mode == "success" {
					var html string
					text := "Salary CHF"
					if scraper == "workday" {
						text = "Build reliable services."
					}
					if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&html); err != nil || title != "Senior Software Engineer" || descriptions != 1 || !strings.Contains(html, text) {
						t.Fatal("canonical title/description lost", err)
					}
				} else if title != "Original" || descriptions != 0 || reserved != (mode == "reserved") {
					t.Fatal("policy/failure changed canonical content")
				}
				current, err := authority.ReadWorkdayDetail(ctx, claim)
				_ = current // A settled attempt must no longer grant a read.
				if err == nil {
					t.Fatal("settled attempt retained read authority")
				}
				domain := "example.com"
				if scraper == "workday" {
					domain = "fixture.wd1.myworkdayjobs.com"
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:"+domain, f.original).Result()
				if err != nil || lease != nil || !due.After(time.Now()) || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("canonical deadline or lease not conserved", err)
				}
			})
		}
	}
}
