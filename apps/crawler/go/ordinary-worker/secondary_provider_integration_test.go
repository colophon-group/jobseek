package worker

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"
)

func TestRealSecondaryProviderReferenceHTTPCommitsCanonicalEffects(t *testing.T) {
	for _, c := range secondaryHTTPCases(t) {
		t.Run(c.Provider+"/"+c.Name, func(t *testing.T) {
			md := map[string]any{}
			if json.Unmarshal(c.Metadata, &md) != nil {
				t.Fatal("invalid metadata")
			}
			// The exact current scraper configurations are tested separately; these
			// references isolate canonical monitor settlement and URL-only scheduling.
			md["scraper_type"] = "skip"
			if c.Provider == "softgarden" {
				md["scraper_type"] = "json-ld"
			}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, c.Provider, string(raw), c.BoardURL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, secondaryHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
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
				if c.Provider == "softgarden" {
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
				if due {
					t.Fatal("skip detail assignment invented scrape")
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

func TestRealSecondaryResourceReservationPreservesCanonicalContent(t *testing.T) {
	for _, provider := range []string{"bamboohr", "recruiter_co_kr"} {
		t.Run(provider, func(t *testing.T) {
			var c secondaryHTTPCase
			for _, ref := range secondaryHTTPCases(t) {
				if ref.Provider == provider && ref.Name == "complete" {
					c = ref
					break
				}
			}
			c.Pages = map[string]json.RawMessage{}
			encode := func(body string, status int, reserved bool) json.RawMessage {
				headers := map[string]string{}
				if reserved {
					headers["TDM-Reservation"] = "1"
				}
				raw, e := json.Marshal(map[string]any{"body": body, "status": status, "headers": headers})
				if e != nil {
					t.Fatal(e)
				}
				return raw
			}
			md := map[string]any{"scraper_type": "skip"}
			if provider == "bamboohr" {
				md["description_include_regex"] = "Build"
				c.Pages[c.Endpoint] = encode(`{"meta":{"totalCount":2},"result":[{"id":123,"jobOpeningName":"Engineer"},{"id":456,"jobOpeningName":"Designer"}]}`, 200, false)
				c.Pages["https://acme.bamboohr.com/careers/123/detail"] = encode(`{}`, 503, false)
				c.Pages["https://acme.bamboohr.com/careers/456/detail"] = encode(`{}`, 200, true)
			} else {
				c.Pages[c.Endpoint] = encode(`{"pagination":{"totalPages":1},"list":[{"positionSn":123,"title":"Engineer"},{"positionSn":456,"title":"Designer"}]}`, 200, false)
				c.Pages["https://api-recruiter.recruiter.co.kr/position/v2/jobflex/123"] = encode(`{}`, 503, false)
				c.Pages["https://api-recruiter.recruiter.co.kr/position/v2/jobflex/456"] = encode(`{}`, 200, true)
			}
			raw, e := json.Marshal(md)
			if e != nil {
				t.Fatal(e)
			}
			f := privateRichPipelineFixtureURL(t, provider, string(raw), c.BoardURL)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			requests := []secondaryHTTPRequest{}
			var mutex sync.Mutex
			result, e := RunGreenhouseClaim(ctx, f.a, claim, secondaryHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled || result.Cycle.Status != "publisher_reserved" || result.Batches.Inserted != 0 {
				t.Fatal("detail policy did not settle reservation", e)
			}
			assertRichDeadlineAndLease(t, f, provider)
			var reserved bool
			var source string
			if e := f.pg.QueryRow(ctx, "SELECT tdm_reserved,tdm_reservation->>'url' FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &source); e != nil {
				t.Fatal(e)
			}
			if !reserved || !strings.Contains(source, "456") {
				t.Fatal("canonical reservation lost exact emitting resource")
			}
		})
	}
}

func TestRealSecondaryEnrichmentRefreshPreservesScrapedDescriptionAndLocales(t *testing.T) {
	for _, provider := range []string{"ukg", "bamboohr"} {
		for _, mode := range []string{"touched", "relisted"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				var c secondaryHTTPCase
				for _, ref := range secondaryHTTPCases(t) {
					if ref.Provider == provider && ref.Name == "complete" {
						c = ref
						break
					}
				}
				scraper := "embedded"
				fields := []string{"description"}
				if provider == "bamboohr" {
					scraper = "api_sniffer"
					fields = []string{"description", "locations", "employment_type", "job_location_type", "date_posted"}
				}
				md := map[string]any{"scraper_type": scraper, "scraper_config": map[string]any{"enrich": fields}}
				raw, e := json.Marshal(md)
				if e != nil {
					t.Fatal(e)
				}
				f := privateRichPipelineFixtureURL(t, provider, string(raw), c.BoardURL)
				ctx := context.Background()
				source := c.Expected.URLs[0]
				if _, e := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,titles=ARRAY['Retained title'],employment_type='part_time',location_ids=ARRAY[2],location_types=ARRAY['remote'],locales=ARRAY['de'],is_active=$3,description_r2_hash=123 WHERE id=$1::uuid", f.original, source, mode == "touched"); e != nil {
					t.Fatal(e)
				}
				if _, e := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Delegated authoritative body</p>',123,true)", f.original); e != nil {
					t.Fatal(e)
				}
				claim, circuits := claimFixture(t, f)
				requests := []secondaryHTTPRequest{}
				var mutex sync.Mutex
				result, e := RunGreenhouseClaim(ctx, f.a, claim, secondaryHTTPFixture(t, c, &requests, &mutex), richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("refresh failed", e)
				}
				var title, html string
				var employment *string
				var locales, locationTypes []string
				var locations []int32
				var active, uploaded bool
				if e := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.employment_type,p.location_ids,p.location_types,p.locales,p.is_active,d.html,d.r2_uploaded FROM job_posting p JOIN descriptions d ON d.posting_id=p.id AND d.locale='en' WHERE p.id=$1::uuid", f.original).Scan(&title, &employment, &locations, &locationTypes, &locales, &active, &html, &uploaded); e != nil {
					t.Fatal(e)
				}
				if html != "<p>Delegated authoritative body</p>" || !uploaded || !active {
					t.Fatal("refresh overwrote delegated description or lifecycle")
				}
				// Python's rich refresh replaces monitor-owned structured fields,
				// including missing values; scraped bytes/locales stay delegated.
				if provider == "bamboohr" && (employment != nil || len(locationTypes) != 0 || len(locations) != 0) {
					t.Fatal("monitor-owned structured refresh differs from Python")
				}
				if provider == "ukg" && (employment == nil || *employment != "full_time") {
					t.Fatal("UKG monitor employment refresh lost authority")
				}
				if len(locales) != 1 || locales[0] != "de" {
					t.Fatal("refresh replaced delegated scraped locales")
				}
				if title != "Engineer" {
					t.Fatal("monitor title authority was lost")
				}
				if mode == "touched" && result.Batches.Touched < 1 || mode == "relisted" && result.Batches.Relisted < 1 {
					t.Fatal("refresh used wrong lifecycle")
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}
