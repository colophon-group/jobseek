package worker

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func apiOwnedFixture(t *testing.T, provider string) (nativePipelineFixture, *queue.Authority, *queue.Claim) {
	source := "https://jobs.smartrecruiters.com/Fixture/123-engineer"
	if provider == "workable" {
		source = "https://apply.workable.com/fixture/j/ABC123/"
	}
	return independentDetailOwnedFixture(t, `{"scraper_type":"`+provider+`","selector":"a.job","render":true}`, source)
}

func apiDetailPayload(provider string) string {
	if provider == "workable" {
		return `{"title":"Senior Software Engineer","description":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","requirements":"<p>Build things.</p>","locations":[{"city":"Zurich","country":"Switzerland"}],"employment_type":"full_time"}`
	}
	return `{"name":"Senior Software Engineer","location":{"fullLocation":"Zurich, Switzerland"},"typeOfEmployment":{"label":"Full-time"},"jobAd":{"sections":{"jobDescription":{"text":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"}}}}`
}

func TestRealAPIDetailUsesVerifiedHTTPSharedPersistenceAndOneShotFallback(t *testing.T) {
	for _, mode := range []string{"smartrecruiters", "workable", "workable-markdown"} {
		t.Run(mode, func(t *testing.T) {
			provider := strings.TrimSuffix(mode, "-markdown")
			f, a, claim := apiOwnedFixture(t, provider)
			ctx := context.Background()
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				wantHost, wantPath := "api.smartrecruiters.com", "/v1/companies/Fixture/postings/123-engineer"
				if provider == "workable" {
					wantHost, wantPath = "apply.workable.com", "/api/v2/accounts/fixture/jobs/ABC123"
				}
				if mode == "workable-markdown" && calls == 2 {
					wantPath = "/fixture/jobs/view/ABC123.md"
				}
				if r.Method != "GET" || r.Host != wantHost || r.URL.Path != wantPath {
					t.Error("wrong public API request", r.Method, r.Host, r.URL.Path)
				}
				if mode == "workable-markdown" {
					if calls == 1 {
						w.WriteHeader(429)
						return
					}
					fmt.Fprint(w, "# Senior Software Engineer\n\n## Description\n\nPython. Salary CHF 100000-120000 yearly. 5+ years of experience.\n\n## Apply\n")
				} else {
					fmt.Fprint(w, apiDetailPayload(provider))
				}
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			wantCalls := 1
			if mode == "workable-markdown" {
				wantCalls = 2
			}
			if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != wantCalls || result.HTTP.Requests != int64(wantCalls) || result.HTTP.Responses != int64(wantCalls) {
				t.Fatal("API detail failed", result, err, calls)
			}
			var title, html, currency string
			var due time.Time
			var uploaded bool
			var canonicalHash *int64
			var pendingHash int64
			if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],d.html,p.salary_currency,p.next_scrape_at,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &html, &currency, &due, &uploaded, &canonicalHash, &pendingHash); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || !strings.Contains(html, "Salary CHF") || currency != "CHF" || uploaded || canonicalHash != nil || pendingHash == 0 {
				t.Fatal("API canonical fields or pending description differ")
			}
			if provider == "workable" && mode != "workable-markdown" && !strings.Contains(html, "Build things.") {
				t.Fatal("Workable requirements lost")
			}
			score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("API canonical deadline/lease differs", err)
			}
		})
	}
}

func TestRealAPIDetailFailuresAndOptOutPreserveExistingContentAndSettlement(t *testing.T) {
	for _, provider := range []string{"smartrecruiters", "workable"} {
		for _, mode := range []string{"404-empty", "503-empty", "malformed", "transport", "header-reserved", "meta-reserved", "inactive-header-reserved", "existing-reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				f, a, claim := apiOwnedFixture(t, provider)
				ctx := context.Background()
				if mode == "existing-reserved" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				calls := 0
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					calls++
					status, body := 200, apiDetailPayload(provider)
					switch mode {
					case "404-empty":
						status = 404
					case "503-empty":
						status = 503
					case "malformed":
						body = "{"
					case "header-reserved", "inactive-header-reserved":
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						if mode == "inactive-header-reserved" {
							if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
								t.Fatal(err)
							}
						}
					case "meta-reserved":
						body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">`
					}
					w.WriteHeader(status)
					fmt.Fprint(w, body)
				})
				if mode == "transport" {
					client = &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(*http.Request) (*http.Response, error) { calls++; return nil, io.ErrUnexpectedEOF })}}
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
				if err != nil || !result.Settled {
					t.Fatal("failure did not settle", result, err)
				}
				var title string
				var active, reserved bool
				var due *time.Time
				var descriptions int
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); err != nil {
					t.Fatal(err)
				}
				if title != "Original" || descriptions != 0 || active != (mode != "inactive-header-reserved") {
					t.Fatal("API non-success changed content/visibility", mode)
				}
				if strings.Contains(mode, "reserved") && !reserved {
					t.Fatal("positive opt-out lost")
				}
				wantCalls := 1
				if mode == "existing-reserved" {
					wantCalls = 0
				}
				if calls != wantCalls {
					t.Fatal("API one-shot behavior changed", calls)
				}
				if mode == "inactive-header-reserved" && (due != nil || result.Cycle.Status != "unscheduled") {
					t.Fatal("inactive opt-out rescheduled")
				}
				if mode == "header-reserved" || mode == "meta-reserved" || mode == "inactive-header-reserved" {
					var source, resource string
					if err := f.pg.QueryRow(ctx, "SELECT tdm_reservation->>'source',tdm_reservation->>'url' FROM job_posting WHERE id=$1::uuid", f.original).Scan(&source, &resource); err != nil {
						t.Fatal(err)
					}
					wantSource := "header"
					if mode == "meta-reserved" {
						wantSource = "meta"
					}
					u, _ := url.Parse(resource)
					wantHost := "api.smartrecruiters.com"
					if provider == "workable" {
						wantHost = "apply.workable.com"
					}
					if source != wantSource || u == nil || u.Hostname() != wantHost {
						t.Fatal("reservation provenance lost", source, resource)
					}
				}
				if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("API failure retained lease")
				}
			})
		}
	}
}
