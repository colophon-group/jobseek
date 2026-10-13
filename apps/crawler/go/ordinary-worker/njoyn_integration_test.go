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

func TestRealNjoynURLInventoryCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"complete", "reserved", "failed", "wrong-binding", "foreign-tenant", "partial", "rich-empty", "truncated"} {
		t.Run(mode, func(t *testing.T) {
			board := "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21001&page=joblisting"
			f := privateRichPipelineFixtureURL(t, "njoyn", `{"scraper_type":"json-ld","page_wait_ms":0}`, board, queue.Browser, queue.Browser)
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
				posting := "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21001&page=JobDetails&jobid=123456&brid=1"
				if mode == "foreign-tenant" {
					posting = strings.Replace(posting, "clid=21001", "clid=21002", 1)
				}
				response := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success", Inventory: json.RawMessage(fmt.Sprintf(`{"Jobs":[{"url":%q}],"URLOnly":true,"Truncated":false}`, posting))}
				switch mode {
				case "reserved":
					response.Outcome = "publisher_reserved"
					response.Inventory = nil
					response.Reservation = &replay.Reservation{URL: board, Source: "header"}
				case "failed":
					response.Outcome = "failed"
					response.Inventory = nil
				case "wrong-binding":
					response.ConfigFingerprint = strings.Repeat("c", 64)
				case "partial":
					response.Outcome = "partial"
				case "rich-empty":
					response.Inventory = json.RawMessage(`{"Jobs":[],"URLOnly":false,"Truncated":false}`)
				case "truncated":
					response.Inventory = json.RawMessage(`{"Jobs":[],"URLOnly":true,"Truncated":true}`)
				}
				return parseAPIReplayResponse(p, response)
			})
			client := richPipelineHTTP(t, func(http.ResponseWriter, *http.Request) { t.Fatal("session monitor used worker direct HTTP") })
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("Njoyn claim settlement", e)
			}
			assertRichDeadlineAndLease(t, f, "njoyn", queue.Browser)
			var inserted, missing, failures int
			var reserved bool
			if e := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if inserted != 1 || missing != 1 || failures != 0 || reserved || f.r.ZCard(ctx, "ft_scrapes_browser:cgi.njoyn.com").Val() != 1 {
					t.Fatal("URL inventory/detail scheduling", inserted, missing, failures)
				}
				var descriptions int
				if e := f.pg.QueryRow(ctx, `SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid`, f.board, f.original).Scan(&descriptions); e != nil || descriptions != 0 {
					t.Fatal("URL-only monitor fabricated detail content", e)
				}
			} else {
				want := 1
				if mode == "reserved" {
					want = 0
				}
				if inserted != 0 || missing != 0 || failures != want || reserved != (mode == "reserved") {
					t.Fatal("incomplete session wrote prefix/absence", inserted, missing, failures, reserved)
				}
			}
		})
	}
}
