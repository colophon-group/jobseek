package worker

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealJobbank104APIDescriptionSchedulingAndRefreshConservation(t *testing.T) {
	var oracle smallProviderOracle
	for _, c := range smallProviderOracleCases(t) {
		if c.Provider == "jobbank104" && c.Name == "populated" {
			oracle = c
			break
		}
	}
	if len(oracle.Pages) != 1 || len(oracle.Expected.Jobs) != 1 {
		t.Fatal("complete original listing oracle required")
	}
	source, ok := oracle.Expected.Jobs[0]["url"].(string)
	if !ok {
		t.Fatal("original posting URL required")
	}
	expected, err := secondaryRichJob(oracle.Expected.Jobs[0])
	if err != nil || expected.Title == nil || expected.Description == nil {
		t.Fatal("original listing fields required")
	}
	for _, proxy := range []bool{false, true} {
		for _, mode := range []string{"new", "touched", "relisted"} {
			t.Run(mode+"/proxy-"+map[bool]string{true: "true", false: "false"}[proxy], func(t *testing.T) {
				options := map[string]any{"api_url": "https://www.104.com.tw/job/ajax/content/{id}", "json_path": "data", "fields": map[string]any{"description": "description"}, "enrich": []string{"description"}}
				metadata, _ := json.Marshal(map[string]any{"proxy": proxy, "scraper_type": "api_sniffer", "scraper_config": options})
				f := privateRichPipelineFixtureURL(t, "jobbank104", string(metadata), oracle.Board.URL)
				ctx := context.Background()
				if mode != "new" {
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,titles=ARRAY['Prior detail title'],locales=ARRAY['de'],is_active=$3,description_r2_hash=123,next_scrape_at=NULL WHERE id=$1::uuid", f.original, source, mode == "touched"); err != nil {
						t.Fatal(err)
					}
					if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Full API authoritative description</p>',123,true)", f.original); err != nil {
						t.Fatal(err)
					}
				}
				claim, circuits := claimFixture(t, f)
				calls := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					calls++
					if r.Method != "GET" || r.Host != "www.104.com.tw" || strings.Contains(r.URL.Path, "/job/ajax/content/") || r.URL.Query().Get("page") != "1" {
						t.Error("listing request acquired detail authority")
					}
					w.Header().Set("Content-Type", "application/json")
					w.Write(oracle.Pages[0])
				}))
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Status != "succeeded" || calls != 1 {
					t.Fatal("delegated listing did not settle", err)
				}
				var id, title, html string
				var locales []string
				var due, uploaded, active bool
				var missing int
				var hash int64
				if err := f.pg.QueryRow(ctx, "SELECT p.id::text,p.titles[1],p.locales,p.next_scrape_at IS NOT NULL,d.html,d.r2_uploaded,p.is_active,p.missing_count,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, source).Scan(&id, &title, &locales, &due, &html, &uploaded, &active, &missing, &hash); err != nil {
					t.Fatal(err)
				}
				if title != *expected.Title || !active || missing != 0 {
					t.Fatal("listing identity/title/liveness authority changed")
				}
				if mode == "new" {
					if html != *expected.Description || uploaded || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source || f.r.HGet(ctx, "scrape:"+id, "domain").Val() != "www.104.com.tw" {
						t.Fatal("new listing lost fallback or independent API schedule")
					}
				} else if html != "<p>Full API authoritative description</p>" || !uploaded || hash != 123 || len(locales) != 1 || locales[0] != "de" {
					t.Fatal("listing refresh replaced API description/hash/locales")
				}
				if mode == "relisted" && !due {
					t.Fatal("relisted source lost API schedule")
				}
				if _, err := queue.InspectHTTPAPIDetail(f.board, claim.Descriptor().Config, source, queue.Simple); err != nil {
					t.Fatal("scheduled API detail rejected", err)
				}
				assertRichDeadlineAndLease(t, f, "jobbank104")
			})
		}
	}
}
