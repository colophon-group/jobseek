package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
)

func TestRealFourthProviderReferenceHTTPCommitsCanonicalEffects(t *testing.T) {
	for _, c := range fourthHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal(c.Metadata, &md) != nil {
				t.Fatal("invalid metadata")
			}
			// The exact current scraper configurations are tested separately; these
			// references isolate canonical monitor settlement and URL-only scheduling.
			md["scraper_type"] = c.Provider
			if c.Provider == "paycom" {
				md["scraper_config"] = map[string]any{"enrich": []string{"title", "description", "locations", "employment_type", "job_location_type", "date_posted", "base_salary"}}
			}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, c.Provider, string(raw), c.BoardURL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, fourthHTTPFixture(t, c.Pages, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("provider did not settle canonically", e)
			}
			assertRichDeadlineAndLease(t, f, c.Provider)
			var failures, gone int
			var reserved bool
			if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
				t.Fatal(e)
			}
			if reserved {
				t.Fatal("reference invented publisher reservation")
			}
			if c.Expected.Gone {
				if failures != 0 || gone != 1 || result.Batches.Inserted != 0 {
					t.Fatal("gone became content/failure")
				}
				return
			}
			if c.Expected.Error {
				if failures != 1 || gone != 0 || result.Batches.Inserted != 0 {
					t.Fatal("failed inventory reached canonical content")
				}
				return
			}
			if failures != 0 || gone != 0 {
				t.Fatal("valid inventory recorded as failure")
			}
			for _, source := range c.Expected.URLs {
				var id string
				var title *string
				var due bool
				if e := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &title, &due); e != nil {
					t.Fatal(e)
				}
				if c.Provider == "rippling" {
					if title != nil || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
						t.Fatal("URL-only source lost native detail scheduling")
					}
					continue
				}
				var ref map[string]any
				for _, job := range c.Expected.Jobs {
					if job["url"] == source {
						ref = job
					}
				}
				if ref == nil || title == nil || *title != ref["title"] {
					t.Fatal("canonical title differs from actual Python output")
				}
				if !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
					t.Fatal("hybrid detail scheduling lost")
				}
				if description, ok := ref["description"].(string); ok && description != "" {
					var html string
					if e := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid ORDER BY locale LIMIT 1", id).Scan(&html); e != nil {
						t.Fatal(e)
					}
					if !strings.Contains(html, description) {
						t.Fatal("canonical description differs")
					}
				}
			}
		})
	}
}

func TestRealPaycomHybridRefreshPreservesTouchedAndRelistedDetailContent(t *testing.T) {
	for _, mode := range []string{"touched", "relisted"} {
		t.Run(mode, func(t *testing.T) {
			var c fourthHTTPCase
			for _, ref := range fourthHTTPCases(t) {
				if ref.Provider == "paycom" && ref.Name == "complete" {
					c = ref
					break
				}
			}
			source := ""
			for _, url := range c.Expected.URLs {
				source = url
				break
			}
			if source == "" {
				t.Fatal("missing rich reference member")
			}
			var md map[string]any
			if err := json.Unmarshal(c.Metadata, &md); err != nil {
				t.Fatal(err)
			}
			md["scraper_type"] = "paycom"
			md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
			raw, err := json.Marshal(md)
			if err != nil {
				t.Fatal(err)
			}
			f := privateRichPipelineFixtureURL(t, "paycom", string(raw), c.BoardURL)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,titles=ARRAY['Authoritative detail'],employment_type='part_time',location_ids=ARRAY[2],is_active=$3,description_r2_hash=123 WHERE id=$1::uuid", f.original, source, mode == "touched"); err != nil {
				t.Fatal(err)
			}
			if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Authoritative detail body</p>',123,true)", f.original); err != nil {
				t.Fatal(err)
			}
			claim, circuits := claimFixture(t, f)
			requests := []fourthHTTPRequest{}
			var mutex sync.Mutex
			result, err := RunGreenhouseClaim(ctx, f.a, claim, fourthHTTPFixture(t, c.Pages, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("hybrid refresh failed", err)
			}
			var titles []string
			var employment, html string
			var locations []int32
			var active, uploaded bool
			if err := f.pg.QueryRow(ctx, "SELECT p.titles,p.employment_type,p.location_ids,p.is_active,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.id=$1::uuid", f.original).Scan(&titles, &employment, &locations, &active, &html, &uploaded); err != nil {
				t.Fatal(err)
			}
			if fmt.Sprint(titles) != "[Authoritative detail]" || employment != "part_time" || fmt.Sprint(locations) != "[2]" || !active || html != "<p>Authoritative detail body</p>" || !uploaded {
				t.Fatal("hybrid inventory replaced authoritative detail content")
			}
			if mode == "touched" && result.Batches.Touched < 1 || mode == "relisted" && result.Batches.Relisted < 1 {
				t.Fatal("existing member did not use expected lifecycle", result.Batches)
			}
			assertRichDeadlineAndLease(t, f, "paycom")
		})
	}
}
