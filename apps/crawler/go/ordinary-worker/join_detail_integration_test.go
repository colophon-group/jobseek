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
	"github.com/redis/go-redis/v9"
)

const nativeJoinDetailMetadata = `{"scraper_type":"nextdata","render":true,"scraper_config":{"path":"props.pageProps.initialState.job","fields":{"title":"title","description":"schemaDescription || unifiedDescription || description","locations":"city.cityName","employment_type":"employmentType.googleType","job_location_type":"workplaceType","date_posted":"createdAt"}}}`
const nativeJoinDetailHTML = `<script id="__NEXT_DATA__">{"props":{"pageProps":{"initialState":{"job":{"title":"Senior Software Engineer","schemaDescription":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","city":{"cityName":"Zurich"},"employmentType":{"googleType":"FULL_TIME"},"workplaceType":"REMOTE","createdAt":"2026-10-04"}}}}}</script>`

func TestRealOwnedJoinDetailUsesExistingExtractionAndCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"success", "redirect", "404", "503", "malformed", "transport", "header", "meta", "inactive-header"} {
		t.Run(mode, func(t *testing.T) {
			f, a, claim := independentDetailOwnedFixture(t, nativeJoinDetailMetadata, "https://join.com/companies/fixture/123-engineer")
			ctx := context.Background()
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "join.com" {
					t.Error("configured detail changed its direct request")
				}
				if mode == "redirect" && calls == 1 {
					http.Redirect(w, r, "/job-content", 302)
					return
				}
				body := nativeJoinDetailHTML
				switch mode {
				case "404":
					w.WriteHeader(404)
				case "503":
					w.WriteHeader(503)
				case "malformed":
					body = `<script id="__NEXT_DATA__">{broken</script>`
				case "header", "inactive-header":
					if mode == "inactive-header" {
						if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
							t.Fatal(err)
						}
					}
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "join-detail-policy")
					w.WriteHeader(404)
				case "meta":
					body = `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="join-detail-policy">`
				}
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
			if err != nil || result == nil || !result.Settled {
				t.Fatalf("Join detail failed its owned settlement: %+v %v", result, err)
			}
			var title string
			var active, reserved bool
			var due *time.Time
			var descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); err != nil {
				t.Fatal(err)
			}
			if mode == "success" || mode == "redirect" {
				var html, currency string
				if err := f.pg.QueryRow(ctx, `SELECT d.html,p.salary_currency FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.id=$1::uuid`, f.original).Scan(&html, &currency); err != nil {
					t.Fatal(err)
				}
				if result.Cycle.Status != "succeeded" || title != "Senior Software Engineer" || descriptions != 1 || !strings.Contains(html, "Salary CHF") || currency != "CHF" || reserved || !active {
					t.Fatal("Join extraction/shared enrichment differs", result.Cycle, title, currency)
				}
			} else {
				if title != "Original" || descriptions != 0 || active != (mode != "inactive-header") {
					t.Fatal("unsuccessful extraction changed existing content or visibility")
				}
				wantReserved := mode == "header" || mode == "meta" || mode == "inactive-header"
				wantStatus := "publisher_reserved"
				if mode == "inactive-header" {
					wantStatus = "unscheduled"
				}
				if reserved != wantReserved || wantReserved && result.Cycle.Status != wantStatus {
					t.Fatal("positive publisher policy lost", result.Cycle)
				}
			}
			wantCalls := 1
			if mode == "redirect" {
				wantCalls = 2
			}
			if calls != wantCalls {
				t.Fatal("detail changed its one-shot/redirect request boundary", calls)
			}
			score, err := f.r.ZScore(ctx, "scrapes_simple:join.com", f.original).Result()
			if due == nil && err != redis.Nil || due != nil && (err != nil || score != float64(due.UnixMicro())/1e6) || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("actual posting-host canonical deadline or lease differs", err)
			}
		})
	}
}
