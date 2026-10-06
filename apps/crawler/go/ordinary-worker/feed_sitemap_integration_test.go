package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"testing"
)

func TestRealOwnedSitemapRewritesCanonicalDetailIdentity(t *testing.T) {
	for _, c := range []struct{ Name, Source, Find, Replacement, Expected string }{
		{"iframe", "https://example.com/jobs/123/job", "$", "?in_iframe=1", "https://example.com/jobs/123/job?in_iframe=1"},
		{"suffix", "https://example.com/jobs/123/job", "/job$", "/job?in_iframe=1", "https://example.com/jobs/123/job?in_iframe=1"},
		{"query", "https://example.com/jobs/123?lang=en", `\?lang=.*$`, "", "https://example.com/jobs/123"},
		{"upgrade", "http://example.com/vacancies/123/engineer", "^http://", "https://", "https://example.com/vacancies/123/engineer"},
	} {
		t.Run(c.Name, func(t *testing.T) {
			metadata, err := json.Marshal(map[string]any{"sitemap_url": "https://example.com/jobs.xml", "url": "https://unused.example.com/sitemap.xml", "scraper_type": "json-ld", "url_transform": map[string]string{"find": c.Find, "replace": c.Replacement}})
			if err != nil {
				t.Fatal(err)
			}
			f := privateRichPipelineFixture(t, "sitemap", string(metadata))
			claim, circuits := claimFixture(t, f)
			requests := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests++
				if r.Host != "example.com" || r.URL.Path != "/jobs.xml" || r.Method != "GET" {
					t.Error("posting rewrite changed fetch authority")
				}
				fmt.Fprintf(w, `<urlset><url><loc>%s</loc></url></urlset>`, c.Source)
			}))
			result, err := RunGreenhouseClaim(context.Background(), f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || result == nil || !result.Settled || result.Batches.Inserted != 1 || requests != 1 {
				t.Fatal(result, err)
			}
			var id, source string
			var pending bool
			err = f.pg.QueryRow(context.Background(), `SELECT id::text,source_url,next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2`, f.board, c.Expected).Scan(&id, &source, &pending)
			if err != nil || source != c.Expected || !pending {
				t.Fatal("canonical detail identity/deadline changed", source, pending, err)
			}
			cached := f.r.HGet(context.Background(), "scrape:"+id, "source_url").Val()
			if cached != c.Expected || f.r.ZScore(context.Background(), "ft_scrapes_simple:example.com", id).Err() != nil {
				t.Fatal("rewritten canonical identity lost queue/cache publication", cached)
			}
			assertRichDeadlineAndLease(t, f, "sitemap")
		})
	}
}
