package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestRealFifthProviderDetailEnrichmentPreservesSelectedAuthorityAndBackfillsEmptyFields(t *testing.T) {
	for _, route := range []string{"adp", "paylocity", "paylocity/proxy"} {
		provider := strings.Split(route, "/")[0]
		for _, mode := range []string{"description-mask", "empty-backfill"} {
			t.Run(route+"/"+mode, func(t *testing.T) {
				c := fifthPolicyReference(t, provider, true, "complete")
				c.Metadata["enrich"] = []string{"description"}
				if route == "paylocity/proxy" {
					c.Name += "/proxy"
					c.Metadata["proxy"] = true
				}
				metadata, e := json.Marshal(map[string]any{"scraper_type": provider, "scraper_config": c.Metadata})
				if e != nil {
					t.Fatal(e)
				}
				f, a, claim := independentDetailOwnedFixture(t, string(metadata), c.Source)
				ctx := context.Background()
				if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); e != nil {
					t.Fatal(e)
				}
				if mode == "empty-backfill" {
					if _, e = f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY[]::text[],employment_type=NULL,location_ids=ARRAY[]::integer[] WHERE id=$1::uuid", f.original); e != nil {
						t.Fatal(e)
					}
				}
				requests := []fourthHTTPRequest{}
				var mutex sync.Mutex
				circuits, e := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if e != nil {
					t.Fatal(e)
				}
				result, e := RunDetail(ctx, a, claim, fifthExecutionHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f).Processor, circuits)
				if e != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" {
					t.Fatal("masked detail did not settle", result, e)
				}
				var title, html string
				var employment *string
				var locations []int32
				var due time.Time
				if e = f.pg.QueryRow(ctx, "SELECT p.titles[1],p.employment_type,p.location_ids,p.next_scrape_at,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&title, &employment, &locations, &due, &html); e != nil {
					t.Fatal(e)
				}
				if !strings.Contains(html, *c.Expected.CanonicalDescription) {
					t.Fatal("selected description lost")
				}
				if mode == "description-mask" && (title != "Monitor title" || employment == nil || *employment != "part_time" || fmt.Sprint(locations) != "[2]") {
					t.Fatal("mask overwrote retained monitor fields")
				}
				if mode == "empty-backfill" && title != c.Expected.Content["title"] {
					t.Fatal("empty title did not backfill")
				}
				score, e := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
				if e != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("enrichment lost deadline or lease", e)
				}
			})
		}
	}

}
