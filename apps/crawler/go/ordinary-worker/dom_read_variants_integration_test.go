package worker

import (
	"context"
	"encoding/json"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestDOMGroupedReadVariantsCanonicalWriterAndLinkedPolicy(t *testing.T) {
	for _, mode := range []string{"linked", "complete", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			config := map[string]any{
				"steps":               []any{map[string]any{"tag": "h1", "field": "title"}, map[string]any{"tag": "p", "field": "description", "html": true}},
				"fetch_url_transform": map[string]any{"find": "^https://example.com/", "replace": "https://example.net/"},
				"linked_description":  map[string]any{"selector": "a.detail", "allowed_host_suffixes": []string{".example.net"}, "min_chars": 50, "steps": []any{map[string]any{"tag": "p", "field": "description", "html": true, "to_end": true}}},
			}
			md, _ := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": config})
			f, a := independentDetailOwnedSetup(t, string(md), "", queue.Simple, "https://example.com/careers")
			ctx := context.Background()
			claim, err := a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("owned claim missing", err)
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.URL.Path == "/linked" {
					if mode == "reserved" {
						w.Header().Set("TDM-Reservation", "1")
					}
					fmt.Fprint(w, "<p>"+strings.Repeat("Complete linked role. ", 10)+"</p>")
				} else {
					body := `<h1>Canonical Engineer</h1><p><a class="detail" href="https://department.example.net/linked">Learn more</a></p>`
					if mode == "complete" {
						body = `<h1>Canonical Engineer</h1><p>` + strings.Repeat("Complete main role. ", 10) + `</p>`
					}
					fmt.Fprint(w, body)
				}
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("native variant did not settle", err)
			}
			var title, source string
			var reserved bool
			var due *time.Time
			var lease *time.Time
			var descriptions int
			err = f.pg.QueryRow(ctx, `SELECT titles[1],source_url,tdm_reserved,next_scrape_at,leased_until,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &source, &reserved, &due, &lease, &descriptions)
			if err != nil || source != "https://example.com/job/"+f.original || lease != nil || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("canonical identity or lease changed", err)
			}
			if mode == "reserved" {
				if !reserved || title != "Original" || descriptions != 0 {
					t.Fatal("linked policy changed canonical content")
				}
			} else {
				if result.Cycle.Status != "succeeded" || title != "Canonical Engineer" || descriptions != 1 || due == nil {
					t.Fatal("native canonical write failed")
				}
				score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
				if err != nil || score != float64(due.UnixMicro())/1e6 {
					t.Fatal("recurring deadline differs", err)
				}
			}
			expected := 2
			if mode == "complete" {
				expected = 1
			}
			if calls != expected || result.HTTP.Requests != int64(expected) {
				t.Fatal("secondary request contract changed", calls, result.HTTP.Requests)
			}
		})
	}
}
