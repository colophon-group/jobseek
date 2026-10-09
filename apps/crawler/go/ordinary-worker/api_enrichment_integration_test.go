package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealConfiguredAPIEnrichmentSchedulesDetailsAndPreservesScrapedContent(t *testing.T) {
	realAPIEnrichmentTransportCases(t, false)
}

func realAPIEnrichmentTransportCases(t *testing.T, annotated bool) {
	for _, mode := range []string{"new", "touched", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "name", "description": "body"}, "scraper_type": "json-ld", "scraper_config": map[string]any{"enrich": []string{"description"}}}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixture(t, "api_sniffer", sharedAnnotationFixtureMetadata(t, string(raw), annotated))
			ctx := context.Background()
			source := "https://example.com/job/" + f.company + "/delegated"
			if mode != "new" {
				if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,titles=ARRAY['Retained title'],locales=ARRAY['de'],is_active=$3,description_r2_hash=123,next_scrape_at=NULL WHERE id=$1::uuid", f.original, source, mode == "touched"); e != nil {
					t.Fatal(e)
				}
				if _, e := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Scraped authoritative description</p>',123,true)", f.original); e != nil {
					t.Fatal(e)
				}
			}
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				fmt.Fprintf(w, `{"jobs":[{"url":%q,"name":"Engineer","body":"<p>Listing teaser</p>"}]}`, source)
			}))
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("configured API enrichment did not settle", e)
			}
			var id, title, html string
			var locales []string
			var due, uploaded bool
			if e := f.pg.QueryRow(ctx, "SELECT p.id::text,p.titles[1],p.locales,p.next_scrape_at IS NOT NULL,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.board_id=$1::uuid AND p.source_url=$2", f.board, source).Scan(&id, &title, &locales, &due, &html, &uploaded); e != nil {
				t.Fatal(e)
			}
			if title != "Engineer" {
				t.Fatal("listing lost title authority")
			}
			if mode == "new" {
				if html != "<p>Listing teaser</p>" || uploaded || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("new row lost fallback or independent detail scheduling")
				}
			} else {
				if html != "<p>Scraped authoritative description</p>" || !uploaded || len(locales) != 1 || locales[0] != "de" {
					t.Fatal("refresh replaced scraped description/locales")
				}
			}
			if mode == "relisted" && !due {
				t.Fatal("relisted enrichment row lost detail schedule")
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
		})
	}
}
