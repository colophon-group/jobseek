package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealNativeBrowserProviderCanonicalSettlement(t *testing.T) {
	for _, provider := range []string{"darwinbox", "bytedance"} {
		for _, mode := range []string{"complete", "truncated", "partial", "reserved", "failed", "wrong-binding", "gone"} {
			if provider == "bytedance" && (mode == "partial" || mode == "gone") {
				continue
			}
			t.Run(provider+"/"+mode, func(t *testing.T) {
				board := "https://airtel.darwinbox.in/ms/candidate/careers"
				if provider == "bytedance" {
					board = "https://joinbytedance.com/search"
				}
				f := privateRichPipelineFixtureURL(t, provider, `{"scraper_type":"skip"}`, board, queue.Browser)
				ctx := context.Background()
				claim, e := f.a.Claim(ctx, queue.Browser)
				if e != nil || claim == nil {
					t.Fatal(e)
				}
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				renderer := heldMonitor(func(_ context.Context, p queue.GreenhouseMonitorProfile, _ map[string]string) (RichDiscovery, error) {
					jobURL := "https://airtel.darwinbox.in/ms/candidatev2/main/careers/jobDetails/" + f.company
					if provider == "bytedance" {
						jobURL = "https://joinbytedance.com/search/" + f.company
					}
					r := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success", Inventory: json.RawMessage(fmt.Sprintf(`{"Jobs":[{"url":%q,"title":"Senior Software Engineer","description":"&lt;p&gt;Build Go services in Zurich.&lt;/p&gt;","locations":["Zurich"],"metadata":{"department":"Engineering"},"employment_type":"Full time","date_posted":"2024-02-03"}],"Truncated":%t}`, jobURL, mode == "truncated"))}
					switch mode {
					case "partial":
						r.Outcome = "partial"
					case "reserved":
						r.Outcome = "publisher_reserved"
						r.Inventory = nil
						r.Reservation = &replay.Reservation{URL: p.Endpoint, Source: "header"}
					case "failed":
						r.Outcome = "failed"
						r.Inventory = nil
					case "wrong-binding":
						r.ConfigFingerprint = strings.Repeat("c", 64)
					case "gone":
						r.Outcome = "provider_gone"
						r.Inventory = nil
						r.FailureURL = "https://airtel.darwinbox.in/ms/candidateapi/job/alljobs"
						r.FailureStatus = 410
					}
					return parseAPIReplayResponse(p, r)
				})
				client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("browser provider used worker direct HTTP") })
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("claim settlement", e)
				}
				assertRichDeadlineAndLease(t, f, provider, queue.Browser)
				var inserted, missing, failures int
				var reserved bool
				if e = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); e != nil {
					t.Fatal(e)
				}
				if mode == "complete" || mode == "truncated" || mode == "partial" {
					var title, html string
					var locations []int32
					var uploaded bool
					var canonical *int64
					if e = f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,d.html,d.r2_uploaded,p.description_r2_hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &locations, &html, &uploaded, &canonical); e != nil {
						t.Fatal(e)
					}
					wantMissing, wantFailures := 0, 0
					if mode == "complete" {
						wantMissing = 1
					}
					if mode == "partial" {
						wantFailures = 1
					}
					if inserted != 1 || missing != wantMissing || failures != wantFailures || title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || !strings.Contains(html, "<p>Build Go services in Zurich.</p>") || uploaded || canonical != nil {
						t.Fatal("canonical fields/prefix/absence/R2 authority", inserted, missing, failures, html)
					}
				} else {
					if inserted != 0 {
						t.Fatal("failed inventory wrote jobs")
					}
					if mode == "reserved" && (!reserved || failures != 0) {
						t.Fatal("publisher outcome changed")
					}
					if (mode == "failed" || mode == "wrong-binding") && (failures != 1 || missing != 0) {
						t.Fatal("failure mutated absence authority")
					}
				}
			})
		}
	}
}
