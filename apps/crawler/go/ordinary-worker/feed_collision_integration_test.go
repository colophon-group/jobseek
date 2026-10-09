package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealFeedCollisionPreservesCanonicalContentAndFailedInventory(t *testing.T) {
	for _, provider := range []string{"rss", "api_sniffer", "dom"} {
		for _, mode := range []string{"complete", "identity-mismatch", "duplicate-conflict", "reserved"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				transform := map[string]any{"find": `^https://example\.com/(?:en|de)/jobs/([a-z0-9-]+)$`, "replace": `https://example.com/jobs/\1`, "collision_policy": "prefer_source_pattern", "collision_preferred_source_patterns": []string{"/en/", "/de/"}, "collision_canonical_identity_regex": `^https://example\.com/jobs/([a-z0-9-]+)$`, "collision_source_identity_regex": `^https://example\.com/(?:en|de)/jobs/([a-z0-9-]+)$`, "collision_stream_buffer_limit": 500}
				md := map[string]any{"scraper_type": "skip", "url_transform": transform}
				switch provider {
				case "rss":
					md["preset"] = "generic"
					md["feed_url"] = "https://example.com/feed"
				case "api_sniffer":
					md["api_url"] = "https://example.com/api"
					md["json_path"] = "jobs"
					md["url_field"] = "url"
					md["fields"] = map[string]any{"title": "title", "description": "description"}
				case "dom":
					md["rich_rows"] = map[string]any{"row_selector": "article", "link_selector": "a", "description_selector": ".description", "default_locations": []string{"Zurich"}}
				}
				if mode == "identity-mismatch" {
					transform["collision_canonical_identity_regex"] = `^https://example\.com/jobs/(WRONG)$`
				}
				raw, _ := json.Marshal(md)
				f := privateRichPipelineFixture(t, provider, string(raw))
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				claim, circuits := claimFixture(t, f)
				canonical := "https://example.com/jobs/" + f.company
				en := "https://example.com/en/jobs/" + f.company
				de := "https://example.com/de/jobs/" + f.company
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					urls := []string{de, en}
					titles := []string{"Deutsch", "Software Engineer"}
					if mode == "duplicate-conflict" {
						urls = []string{en, en}
						titles = []string{"First", "Different"}
					}
					switch provider {
					case "api_sniffer":
						jobs := []map[string]string{}
						for i, u := range urls {
							jobs = append(jobs, map[string]string{"url": u, "title": titles[i], "description": "<p>Build systems.</p>"})
						}
						json.NewEncoder(w).Encode(map[string]any{"jobs": jobs})
					case "rss":
						fmt.Fprint(w, "<rss><channel>")
						for i, u := range urls {
							fmt.Fprintf(w, "<item><link>%s</link><title>%s</title><description><![CDATA[<p>Build systems.</p>]]></description></item>", u, titles[i])
						}
						fmt.Fprint(w, "</channel></rss>")
					case "dom":
						for i, u := range urls {
							fmt.Fprintf(w, `<article><a href="%s">%s</a><div class="description"><p>Build systems.</p></div></article>`, u, titles[i])
						}
					}
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("collision cycle did not settle", result, err)
				}
				assertRichDeadlineAndLease(t, f, provider)
				if mode != "complete" {
					want := "failed"
					if mode == "reserved" {
						want = "publisher_reserved"
					}
					if result.Cycle.Status != want || result.Batches.Inserted != 0 {
						t.Fatal("bad inventory reached canonical writes", result)
					}
					var count, missing int
					var active bool
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil || count != 1 {
						t.Fatal("failed alias inventory changed posting count", count, err)
					}
					if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil || !active || missing != 3 {
						t.Fatal("failed alias inventory advanced absence", err)
					}
					return
				}
				var title, html string
				if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, canonical).Scan(&title, &html); err != nil || title != "Software Engineer" || !strings.Contains(html, "<p>Build systems.</p>") {
					t.Fatal("canonical winning fields lost", title, html, err)
				}
				if result.Cycle.Status != "succeeded" || result.Batches.Inserted != 1 {
					t.Fatal("aliases persisted separately", result)
				}
				var aliases int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url=ANY($2::text[])", f.board, []string{en, de}).Scan(&aliases); err != nil || aliases != 0 {
					t.Fatal("raw aliases became canonical rows", aliases, err)
				}
			})
		}
	}
}
