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

func domOwnedFixture(t *testing.T, proxyFlags ...bool) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	proxy := len(proxyFlags) > 0 && proxyFlags[0]
	return independentDetailOwnedFixture(t, proxyDetailFixtureMetadata(t, `{"scraper_type":"dom","selector":"a.job","render":true,"scraper_config":{"steps":[{"tag":"h1","field":"title"},{"tag":"p","attr":"data-field=location","field":"location"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}],"defaults":{"employment_type":"FULL_TIME","language":"en"}}}`, proxy), "")
}

const nativeDOMHTML = `<html><h1>Senior Software Engineer</h1><p data-field="location">Zurich</p><h2>Role</h2><p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p></html>`

func TestRealDOMDetailUsesVerifiedHTTPSharedEnrichmentAndCanonicalSettlement(t *testing.T) {
	realDOMDetailTransportSuccess(t, false)
}
func TestRealProxyDOMDetailUsesVerifiedHTTPSharedEnrichmentAndCanonicalSettlement(t *testing.T) {
	realDOMDetailTransportSuccess(t, true)
}
func realDOMDetailTransportSuccess(t *testing.T, proxy bool) {
	f, a, claim := domOwnedFixture(t, proxy)
	ctx := context.Background()
	selectedProxy, selectErr := runtimeClaimUsesProxy(ctx, a, claim)
	if selectErr != nil || selectedProxy != proxy {
		t.Fatal("runtime transport lost canonical detail profile", selectedProxy, selectErr)
	}
	calls := 0
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != "GET" || r.URL.Path != "/job/"+f.original {
			t.Error("wrong canonical request")
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		fmt.Fprint(w, nativeDOMHTML)
	})
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	if proxy {
		client = credentialedProxyFixture(t, client)
	}
	result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 || result.HTTP.Requests != 1 || result.HTTP.Responses != 1 || result.TaskKind != queue.Scrape {
		t.Fatal("native DOM execution failed", result, err)
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
		t.Fatal("DOM canonical fields/pending description differ")
	}
	score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("native deadline/lease differs", err)
	}
}

func TestRealDOMDetailPreservesOptOutFailureAndFreshCanonicalPolicy(t *testing.T) {
	realDOMDetailTransportPolicy(t, false)
}
func TestRealProxyDOMDetailPreservesOptOutFailureAndFreshCanonicalPolicy(t *testing.T) {
	realDOMDetailTransportPolicy(t, true)
}
func realDOMDetailTransportPolicy(t *testing.T, proxy bool) {
	for _, mode := range []string{"header-reserved", "meta-reserved", "inactive-header-reserved", "existing-reserved", "fresh-reserved", "404-gone", "gone-redirect", "challenge", "503-transient", "empty", "transport"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"scraper_type":"dom","scraper_config":{"gone_url_pattern":"/Error$","steps":[{"tag":"h1","field":"title"},{"tag":"h2","text":"Role","offset":1,"field":"description","html":true}]}}`
			f, a, claim := independentDetailOwnedFixture(t, proxyDetailFixtureMetadata(t, metadata, proxy), "")
			ctx := context.Background()
			calls := 0
			if mode == "existing-reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				status, body := 200, nativeDOMHTML
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
				case "gone-redirect":
					if r.URL.Path != "/Error" {
						w.Header().Set("Location", "/Error")
						status = 302
					}
				case "challenge":
					body = "<title>Just a moment</title>"
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
			if proxy {
				if mode == "transport" {
					client.proxyRequired = true
				} else {
					client = credentialedProxyFixture(t, client)
				}
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("DOM failure/policy did not settle", err)
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
			wantActive := mode != "404-gone" && mode != "gone-redirect" && mode != "inactive-header-reserved"
			if active != wantActive {
				t.Fatal("DOM changed visibility incorrectly", mode)
			}
			if strings.Contains(mode, "reserved") && !reserved {
				t.Fatal("positive publisher reservation lost", mode)
			}
			if mode == "existing-reserved" && calls != 0 {
				t.Fatal("reserved job fetched")
			}
			if mode == "empty" && calls != 1 {
				t.Fatal("DOM empty extraction unexpectedly retried")
			}
			if mode == "inactive-header-reserved" && (due != nil || result.Cycle.Status != "unscheduled") {
				t.Fatal("inactive opt-out rescheduled")
			}
			if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("failed DOM attempt retained lease")
			}
		})
	}
}
