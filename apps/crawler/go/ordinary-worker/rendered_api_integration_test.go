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

func TestRealRenderedAPIWritesCanonicalContentAndConservesQueue(t *testing.T) {
	for _, mode := range []string{"rich", "truncated", "publisher", "failed", "wrong_binding"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title","description":"body","locations":"city"},"scraper_type":"skip"}`
			f := privateRichPipelineFixture(t, "api_sniffer", metadata, queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			renderer := heldMonitor(func(_ context.Context, p queue.GreenhouseMonitorProfile, _ map[string]string) (RichDiscovery, error) {
				response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success"}
				response.Inventory = json.RawMessage(fmt.Sprintf(`{"Jobs":[{"url":%q,"title":"Senior Software Engineer","description":"<p>Build Go services in Zurich.</p>","locations":["Zurich"],"metadata":{"language":"en"},"extras":{}}],"Truncated":%t,"LastResponseProbe":false}`, "https://example.com/job/"+f.company+"/new", mode == "truncated"))
				switch mode {
				case "publisher":
					response.Outcome = "publisher_reserved"
					response.Inventory = nil
					response.Reservation = &replay.Reservation{URL: "https://example.com/api", Source: "meta"}
				case "failed":
					response.Outcome = "failed"
					response.Inventory = nil
				case "wrong_binding":
					response.ConfigFingerprint = strings.Repeat("c", 64)
				}
				return parseAPIReplayResponse(p, response)
			})
			httpClient := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("browser API used worker direct HTTP") })
			result, err := RunGreenhouseClaim(ctx, f.a, claim, httpClient, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("browser API did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer", queue.Browser)
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "rich" || mode == "truncated" {
				var title, html string
				var locations []int32
				var uploaded bool
				var canonical *int64
				if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,d.html,d.r2_uploaded,p.description_r2_hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &locations, &html, &uploaded, &canonical); err != nil {
					t.Fatal(err)
				}
				wantMissing := 1
				if mode == "truncated" {
					wantMissing = 0
				}
				if inserted != 1 || missing != wantMissing || failures != 0 || title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || !strings.Contains(html, "Build Go services") || uploaded || canonical != nil {
					t.Fatal("canonical browser API fields, delisting or pending description authority changed", inserted, missing, failures)
				}
			} else if inserted != 0 || missing != 0 || mode == "publisher" && (!reserved || failures != 0) || mode != "publisher" && failures != 1 {
				t.Fatal("failure/policy published partial inventory or lost outcome", inserted, missing, failures, reserved)
			}
		})
	}
}
