package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestRealRSSDescriptionEnrichmentSchedulesAndPreservesScrapedContent(t *testing.T) {
	for _, mode := range []string{"new", "touched", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"preset": "generic", "feed_url": "https://example.com/feed", "scraper_type": "json-ld", "scraper_config": map[string]any{"enrich": []string{"description"}}}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixture(t, "rss", string(raw))
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
				fmt.Fprintf(w, `<rss><channel><item><link>%s</link><title>Engineer</title><description><![CDATA[<p>Listing teaser</p>]]></description></item></channel></rss>`, source)
			}))
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("RSS enrichment did not settle", e)
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
			assertRichDeadlineAndLease(t, f, "rss")
		})
	}
}

func TestPublicRSSDescriptionEnrichmentOriginalInventory(t *testing.T) {
	directory := os.Getenv("JOBSEEK_INLINE_PUBLIC_CAPTURE_DIR")
	if directory == "" {
		t.Skip("private complete public feed not supplied")
	}
	raw, err := os.ReadFile(directory + "/native1001-shared-retraites-populaires-careers-public-capture1-2026-10-10.json")
	if err != nil {
		t.Fatal("public capture unavailable")
	}
	var c struct {
		Provider, Status string
		Board            map[string]json.RawMessage
		Jobs             []map[string]any
		Exchanges        []struct {
			Method, URL, Body string
			Status            int
			ContentType       string `json:"content_type"`
		}
		Truncated bool
	}
	if json.Unmarshal(raw, &c) != nil || c.Provider != "rss" || c.Status != "complete" || c.Truncated || len(c.Exchanges) != 1 {
		t.Fatal("original complete capture invalid")
	}
	config := map[string]string{}
	for k, v := range c.Board {
		if k == "metadata" {
			config[k] = string(v)
		} else {
			var s string
			if json.Unmarshal(v, &s) == nil {
				config[k] = s
			}
		}
	}
	p, err := queue.InspectRichMonitor("11111111-1111-4111-8111-111111111111", config)
	if err != nil {
		t.Fatal("original RSS enrichment profile unsupported", err)
	}
	requests := 0
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		x := c.Exchanges[0]
		if requests != 1 || r.Method != x.Method || "https://"+r.Host+r.URL.RequestURI() != x.URL {
			t.Error("original feed request changed")
		}
		w.Header().Set("Content-Type", x.ContentType)
		w.WriteHeader(x.Status)
		fmt.Fprint(w, x.Body)
	}))
	got, err := DiscoverRichMonitor(context.Background(), client.client, p)
	if err != nil || got.Truncated || len(got.Jobs) != len(c.Jobs) || requests != 1 {
		t.Fatal("original complete feed changed", err)
	}
	sort.Slice(got.Jobs, func(i, j int) bool { return got.Jobs[i].URL < got.Jobs[j].URL })
	for n, j := range got.Jobs {
		fields := map[string]any{"url": j.URL, "title": j.Title, "description": j.Description, "locations": j.Locations, "metadata": j.Metadata, "employment_type": j.EmploymentType, "date_posted": j.DatePosted, "source_identity": nullableNextdataIdentity(j.SourceIdentity)}
		b, _ := json.Marshal(fields)
		var actual map[string]any
		json.Unmarshal(b, &actual)
		for k, want := range c.Jobs[n] {
			if !reflect.DeepEqual(actual[k], want) {
				t.Fatal("original feed field changed", n, k)
			}
		}
	}
}
