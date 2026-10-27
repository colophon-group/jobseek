package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"testing"
)

func TestRealAshbyJSONLDSalaryEnrichmentSchedulesAndPreservesMonitorFields(t *testing.T) {
	for _, mode := range []string{"new", "touched", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "ashby", `{"token":"fixture","scraper_type":"json-ld","scraper_config":{"enrich":["base_salary"]}}`)
			ctx := context.Background()
			source := "https://example.com/job/" + f.company + "/salary"
			if mode != "new" {
				if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET source_url=$2,titles=ARRAY['Retained title'],is_active=$3,next_scrape_at=NULL WHERE id=$1::uuid`, f.original, source, mode == "touched"); err != nil {
					t.Fatal(err)
				}
			}
			claim, circuits := claimFixture(t, f)
			description := "<p>Build Go services. Salary CHF 100000-120000 yearly.</p>"
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet || r.Host != "api.ashbyhq.com" || r.URL.Path != "/posting-api/job-board/fixture" || r.URL.Query().Get("includeCompensation") != "true" || len(r.URL.Query()) != 1 {
					t.Error("salary delegation changed Ashby listing request")
				}
				json.NewEncoder(w).Encode(map[string]any{"jobs": []any{map[string]any{"jobUrl": source, "title": "Engineer", "descriptionHtml": description, "employmentType": "FullTime", "workplaceType": "Remote", "isListed": true}}})
			}))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("Ashby selected salary monitor did not settle", err)
			}
			var id, title, html string
			var due bool
			if err := f.pg.QueryRow(ctx, `SELECT p.id::text,p.titles[1],d.html,p.next_scrape_at IS NOT NULL FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.board_id=$1::uuid AND p.source_url=$2`, f.board, source).Scan(&id, &title, &html, &due); err != nil {
				t.Fatal(err)
			}
			cached := f.r.HGet(ctx, "scrape:"+id, "source_url").Val() == source
			if title != "Engineer" || html != description || !due || !cached {
				t.Fatalf("salary delegation: monitor_title=%t monitor_description=%t scheduled=%t cached=%t", title == "Engineer", html == description, due, cached)
			}
			assertRichDeadlineAndLease(t, f, "ashby")
		})
	}
}
