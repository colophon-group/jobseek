package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealBoundedDetailPublicationAcrossMonitorFamilies(t *testing.T) {
	for _, provider := range []string{"dom", "api_sniffer"} {
		for _, proxy := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/proxy=%v", provider, proxy), func(t *testing.T) {
				md := map[string]any{"scraper_type": "json-ld"}
				if proxy {
					md["proxy"] = true
				}
				if provider == "dom" {
					md["link_selector"] = "a.job"
				} else {
					md["api_url"] = "https://example.com/api"
					md["json_path"] = "jobs"
					md["url_field"] = "url"
					md["fields"] = map[string]any{"title": "title"}
					md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, provider, string(raw))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				rows := []map[string]string{}
				html := strings.Builder{}
				for i := 0; i < 65; i++ {
					source := fmt.Sprintf("https://example.com/jobs/%s-%03d", f.company, i)
					rows = append(rows, map[string]string{"url": source, "title": "Engineer"})
					fmt.Fprintf(&html, `<a class="job" href="%s">Engineer</a>`, source)
				}
				body, _ := json.Marshal(map[string]any{"jobs": rows})
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					if provider == "dom" {
						fmt.Fprint(w, html.String())
					} else {
						w.Header().Set("Content-Type", "application/json")
						w.Write(body)
					}
				}))
				if proxy {
					client = credentialedProxyFixture(t, client)
				}
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, nil)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("batched family did not settle", e)
				}
				var inserted, pending, missing int
				if e = f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE description_r2_hash IS NULL AND next_scrape_at IS NOT NULL) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted, &pending); e != nil {
					t.Fatal(e)
				}
				if e = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); e != nil {
					t.Fatal(e)
				}
				if inserted != 65 || pending != 65 || missing != 1 || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 65 {
					t.Fatal("cross-chunk detail conservation differs", inserted, pending, missing)
				}
				for _, row := range rows {
					var id string
					if e = f.pg.QueryRow(ctx, "SELECT id::text FROM job_posting WHERE source_url=$1", row["url"]).Scan(&id); e != nil {
						t.Fatal(e)
					}
					cached := f.r.HGetAll(ctx, "scrape:"+id).Val()
					if cached["source_url"] != row["url"] || cached["board_id"] != f.board || cached["scrape_step"] != "0" || cached["description_r2_hash"] != "" {
						t.Fatal("batch changed original cache contract")
					}
				}
				assertRichDeadlineAndLease(t, f, provider, queue.Simple)
			})
		}
	}
}
