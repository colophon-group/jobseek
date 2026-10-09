package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealRetainedRichConfigsDoNotExecuteOrScheduleInactiveDetails(t *testing.T) {
	for _, provider := range []string{"ashby", "lever", "recruitee"} {
		for _, mode := range []string{"complete", "reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				metadata := `{"token":"fixture","scraper_type":"json-ld","scraper_config":{"render":true,"wait":"networkidle","timeout":30000,"wait_fallback":"domcontentloaded"}}`
				if provider == "recruitee" {
					metadata = `{"api_base":"https://example.com","scraper_type":"json-ld","scraper_config":{"render":true,"wait":"networkidle"}}`
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, "https://example.com/careers", queue.Simple, queue.Browser)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				posting := "https://example.com/job/" + f.company
				description := "<p>Build reliable systems.</p>"
				var payload []byte
				if provider == "ashby" {
					payload, _ = json.Marshal(map[string]any{"jobs": []any{map[string]any{"jobUrl": posting, "title": "Engineer", "descriptionHtml": description}}})
				} else if provider == "lever" {
					payload, _ = json.Marshal([]any{map[string]any{"hostedUrl": posting, "text": "Engineer", "description": description}})
				} else {
					payload, _ = json.Marshal(map[string]any{"offers": []any{map[string]any{"status": "published", "careers_url": posting, "title": "Engineer", "description": description}}})
				}
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(200)
						return
					}
					fmt.Fprint(w, string(payload))
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled || calls != 1 || len(result.Batches.Details) != 0 {
					t.Fatal("inactive detail config executed or unsettled", e, result, calls)
				}
				assertRichDeadlineAndLease(t, f, provider)
				if mode == "reserved" {
					if result.Batches.Inserted != 0 {
						t.Fatal("publisher reservation wrote jobs")
					}
					return
				}
				var count int
				if e = f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2 AND p.titles[1]='Engineer' AND p.next_scrape_at IS NULL AND d.html=$3 AND NOT d.r2_uploaded`, f.board, posting, description).Scan(&count); e != nil || count != 1 {
					t.Fatal("canonical rich/no-enrichment schedule changed", e, count)
				}
			})
		}
	}
}
