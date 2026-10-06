package worker

import (
	"context"
	"encoding/json"
	"fmt"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	"net/http"
	"strings"
	"testing"
)

func TestRealInlineCanonicalEmptyPolicyAlternateIdentityAndEnrichment(t *testing.T) {
	for _, mode := range []string{"rich", "description-mask-new", "description-mask-retained", "reserved", "meta-reserved", "alternate-reserved", "root404", "section-drift", "source-origin-drift", "zero-proof", "explicit-empty", "all-expired", "changed-binding", "json-wrapper", "alternate"} {
		t.Run(mode, func(t *testing.T) {
			md := map[string]any{"scraper_type": "skip", "steps": []any{map[string]any{"tag": "h2", "field": "title"}, map[string]any{"tag": "p", "field": "description", "html": true, "stop_tag": "h2"}}, "defaults": map[string]any{"locations": []string{"Zurich"}}}
			if strings.HasPrefix(mode, "description-mask") {
				md["scraper_type"] = "dom"
				md["scraper_config"] = map[string]any{"enrich": []string{"description"}, "steps": []any{map[string]any{"tag": "p", "field": "description", "html": true}}}
			}
			if mode == "alternate" || mode == "alternate-reserved" {
				md["fetch_urls"] = []string{"https://example.com/unavailable", "https://example.com/alternate"}
			}
			if mode == "section-drift" {
				md["section_start"], md["section_end"] = map[string]any{"text": "Start"}, map[string]any{"text": "End"}
			}
			if mode == "source-origin-drift" {
				md["source_url_selector"], md["source_url_attribute"] = "a", "href"
			}
			if mode == "zero-proof" {
				md["require_zero_proof"] = true
			}
			if mode == "explicit-empty" {
				md["empty_selector"], md["empty_text"] = ".empty", "No vacancies"
				md["require_zero_proof"] = true
			}
			if mode == "all-expired" {
				md["defaults"].(map[string]any)["valid_through"] = "2000-01-01"
				md["exclude_expired"] = true
			}
			if mode == "json-wrapper" {
				md["fetch_json_path"] = "[0].content.rendered"
			}
			encoded, _ := json.Marshal(md)
			f := privateRichPipelineFixture(t, "inline", string(encoded))
			ctx := context.Background()
			url := "https://example.com/careers?_jid=senior-software-engineer-b80d3a"
			// Resolve the exact provider title identity using the same public contract.
			found, err := api.InlineSyntheticURL("https://example.com/careers", "Senior Software Engineer", map[string]int{}, nil)
			if err != nil {
				t.Fatal(err)
			}
			url = found
			if mode == "description-mask-retained" {
				if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET source_url=$2,titles=ARRAY['Old title'],description_r2_hash=123,next_scrape_at=NULL WHERE id=$1::uuid`, f.original, url); err != nil {
					t.Fatal(err)
				}
				if _, err := f.pg.Exec(ctx, `INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded)VALUES($1::uuid,'en','<p>Scraped authoritative body</p>',123,true)`, f.original); err != nil {
					t.Fatal(err)
				}
			}
			claim, circuits := claimFixture(t, f)
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if mode == "changed-binding" {
					if _, err := f.pg.Exec(ctx, `UPDATE job_board SET metadata=jsonb_set(metadata,'{require_zero_proof}','true') WHERE id=$1::uuid`, f.board); err != nil {
						t.Error(err)
					}
				}
				if r.URL.Path == "/unavailable" {
					w.WriteHeader(404)
					return
				}
				if mode == "reserved" || mode == "alternate-reserved" {
					w.Header().Set("TDM-Reservation", "1")
					fmt.Fprint(w, "bad")
					return
				}
				if mode == "meta-reserved" {
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
					return
				}
				if mode == "root404" {
					w.WriteHeader(404)
					return
				}
				if mode == "zero-proof" {
					fmt.Fprint(w, "<h1>No accepted rows</h1>")
					return
				}
				if mode == "source-origin-drift" {
					fmt.Fprint(w, `<a href="https://foreign.example/job">Role</a>`)
				}
				if mode == "explicit-empty" {
					fmt.Fprint(w, `<div class="empty">No vacancies</div>`)
				}
				body := `<h2>Senior Software Engineer</h2><p>Go and PostgreSQL in Zurich.</p>`
				if mode == "json-wrapper" {
					b, _ := json.Marshal([]any{map[string]any{"content": map[string]any{"rendered": body}}})
					w.Write(b)
					return
				}
				fmt.Fprint(w, body)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if mode == "changed-binding" {
				if err == nil || result.Settled {
					t.Fatal("changed binding retained write authority")
				}
				return
			}
			if err != nil || result == nil || !result.Settled {
				t.Fatal(result, err)
			}
			assertRichDeadlineAndLease(t, f, "inline")
			var reserved bool
			var failures, empty int
			var status string
			if err := f.pg.QueryRow(ctx, `SELECT tdm_reserved,consecutive_failures,empty_check_count,board_status FROM job_board WHERE id=$1::uuid`, f.board).Scan(&reserved, &failures, &empty, &status); err != nil {
				t.Fatal(err)
			}
			if strings.Contains(mode, "reserved") {
				if !reserved || result.Batches.Inserted != 0 {
					t.Fatal("publisher reservation wrote inventory")
				}
				return
			}
			if mode == "root404" || mode == "section-drift" || mode == "source-origin-drift" || mode == "zero-proof" {
				if failures != 1 || result.Batches.Inserted != 0 || status == "gone_pending" {
					t.Fatal("invalid document authorized writes or disappearance")
				}
				return
			}
			if mode == "explicit-empty" || mode == "all-expired" {
				if empty != 1 || failures != 0 || result.Batches.Inserted != 0 {
					t.Fatal("verified empty bypassed existing confirmation window")
				}
				return
			}
			if mode == "description-mask-retained" {
				var body string
				var hash int64
				if err := f.pg.QueryRow(ctx, `SELECT html,hash FROM descriptions WHERE posting_id=$1::uuid AND locale='en'`, f.original).Scan(&body, &hash); err != nil || body != "<p>Scraped authoritative body</p>" || hash != 123 {
					t.Fatal("delegated scraped description overwritten", err)
				}
				return
			}
			if result.Batches.Inserted != 1 {
				t.Fatal("rich canonical insert lost")
			}
			var id, title string
			var postedHash *int64
			if err := f.pg.QueryRow(ctx, `SELECT id::text,titles[1],description_r2_hash FROM job_posting WHERE source_url=$1`, url).Scan(&id, &title, &postedHash); err != nil || title != "Senior Software Engineer" || postedHash != nil {
				t.Fatal("canonical title or R2 publication state differs", err)
			}
			var body string
			var pendingHash int64
			var uploaded bool
			if err := f.pg.QueryRow(ctx, `SELECT html,hash,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid AND locale='en'`, id).Scan(&body, &pendingHash, &uploaded); err != nil || !strings.Contains(body, "Go and PostgreSQL") || uploaded {
				t.Fatal("pending description lost", err)
			}
			if mode == "description-mask-new" && f.r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Err() != nil {
				t.Fatal("selected enrichment detail intent lost")
			}
		})
	}
}
