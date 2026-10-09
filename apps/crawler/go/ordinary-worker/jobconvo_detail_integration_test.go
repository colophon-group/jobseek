package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealJobConvoDetailIndependentCanonicalAndQueueSettlement(t *testing.T) {
	var cases []struct {
		Mode                 string
		ProcessedDescription string `json:"processed_description"`
		Exchanges            []struct{ Body string }
		Expected             map[string]any
	}
	body, e := os.ReadFile("../api-sniffer-monitor/testdata/python_jobconvo_detail_requests.json")
	if e != nil || json.Unmarshal(body, &cases) != nil {
		t.Fatal("original detail corpus missing", e)
	}
	raw := ""
	processedDescription := ""
	var expected map[string]any
	for _, c := range cases {
		if c.Mode == "rich" {
			raw, expected, processedDescription = c.Exchanges[0].Body, c.Expected, c.ProcessedDescription
		}
	}
	if raw == "" {
		t.Fatal("complete original detail missing")
	}
	for _, mode := range []string{"complete", "404", "410", "503", "302", "wrong-id", "invalid-json", "header-reserved-404", "body-reserved-503"} {
		t.Run(mode, func(t *testing.T) {
			source := "https://jobs.jobconvo.com/job/engineer/11111111-2222-3333-4444-555555555555/"
			f, owner, claim := independentDetailOwnedFixture(t, `{"scraper_type":"jobconvo","scraper_config":{"locale":"pt-br"}}`, source)
			ctx := context.Background()
			calls := 0
			request, _, e := api.JobConvoDetailRequest(source, "pt-br")
			if e != nil {
				t.Fatal(e)
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if "https://"+r.Host+r.URL.RequestURI() != request.URL || r.Method != "GET" || r.Header.Get("Accept") != "application/json" {
					t.Error("unbound public detail request")
				}
				status, content := 200, raw
				switch mode {
				case "404":
					status = 404
				case "410":
					status = 410
				case "503":
					status = 503
				case "302":
					status = 302
					w.Header().Set("Location", "https://foreign.example/")
				case "wrong-id":
					content = strings.ReplaceAll(raw, "11111111-2222-3333-4444-555555555555", "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
				case "invalid-json":
					content = "{"
				case "header-reserved-404":
					status = 404
					w.Header().Set("TDM-Reservation", "1")
				case "body-reserved-503":
					status = 503
					content = `<meta name="tdm-reservation" content="1">`
					w.Header().Set("Content-Type", "text/html")
				}
				if w.Header().Get("Content-Type") == "" {
					w.Header().Set("Content-Type", "application/json")
				}
				w.WriteHeader(status)
				fmt.Fprint(w, content)
			}))
			circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if e != nil {
				t.Fatal(e)
			}
			result, e := RunDetail(ctx, owner, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("independent detail did not settle", e, result)
			}
			var title string
			var active, reserved bool
			var descriptions int
			var due *time.Time
			if e = f.pg.QueryRow(ctx, `SELECT titles[1],is_active,tdm_reserved,next_scrape_at,(SELECT count(*) FROM descriptions d WHERE d.posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &reserved, &due, &descriptions); e != nil {
				t.Fatal(e)
			}
			if mode == "complete" {
				if title != strings.TrimSpace(fmt.Sprint(expected["title"])) || !active || reserved || due == nil || descriptions != 1 || result.Cycle.Status != "succeeded" {
					t.Fatal("detail canonical fields/schedule changed", title, active, reserved, due, descriptions, result.Cycle.Status)
				}
				var html string
				if e = f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&html); e != nil || html != processedDescription {
					t.Fatal("description/benefits changed", e, html)
				}
			} else {
				if title != "Original" || descriptions != 0 || !active {
					t.Fatal("empty/failed/reserved detail changed old posting", title, descriptions, active)
				}
				if strings.Contains(mode, "reserved") {
					if !reserved || result.Cycle.Status != "publisher_reserved" {
						t.Fatal("detail reservation lost", reserved, result.Cycle.Status)
					}
				} else if due == nil || result.Cycle.Status != "failed" {
					t.Fatal("original empty detail failure/schedule changed", due, result.Cycle.Status)
				}
			}
			if calls != 1 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 || f.r.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
				t.Fatal("detail repeated request or retained lease", calls)
			}
		})
	}
}
