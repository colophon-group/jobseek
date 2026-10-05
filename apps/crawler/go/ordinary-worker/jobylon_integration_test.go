package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealOwnedJobylonRichFieldsDelegatedDescriptionsAndLifecycle(t *testing.T) {
	for _, mode := range []string{"complete", "enrich-new", "enrich-touch", "enrich-retained", "enrich-relist", "enrich-tombstone", "malformed", "bad-item", "gone404", "status410", "status503", "header", "meta", "redirect"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"company_id":"123","scraper_type":"json-ld"}`
			if strings.HasPrefix(mode, "enrich-") {
				metadata = `{"company_id":"123","scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
			}
			f := privateRichPipelineFixture(t, "jobylon", metadata)
			ctx := context.Background()
			url := "https://emp.jobylon.com/jobs/" + f.company + "/"
			existing := mode == "enrich-touch" || mode == "enrich-retained" || mode == "enrich-relist" || mode == "enrich-tombstone"
			retained := mode == "enrich-retained" || mode == "enrich-relist"
			if existing {
				var hash any
				if retained {
					hash = int64(123)
				}
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=NULL,is_active=$3,scrape_failures=$4,description_r2_hash=$5::bigint WHERE id=$1::uuid", f.original, url, mode != "enrich-relist", map[string]int{"enrich-relist": 3, "enrich-tombstone": 3}[mode], hash); err != nil {
					t.Fatal(err)
				}
				if retained {
					if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Delegated description</p>',123,true)", f.original); err != nil {
						t.Fatal(err)
					}
				}
			}
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "cdn.jobylon.com" || r.URL.Path != "/jobs/companies/123/embed/v2/" && !(mode == "redirect" && r.URL.Path == "/redirected") {
					t.Error("embed request left bound endpoint")
				}
				if mode == "redirect" && calls == 1 {
					http.Redirect(w, r, "/redirected", 302)
					return
				}
				switch mode {
				case "gone404":
					w.WriteHeader(404)
					return
				case "status410":
					w.WriteHeader(410)
					return
				case "status503":
					w.WriteHeader(503)
					return
				case "header":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://example.com/policy")
					w.WriteHeader(404)
					return
				case "meta":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1"><meta name="tdm-policy" content="https://example.com/policy">`)
					return
				case "malformed":
					fmt.Fprint(w, "JBL.embed_v2['jobs'] = [{id:1")
					return
				case "bad-item":
					fmt.Fprint(w, "JBL.embed_v2['jobs'] = [null]")
					return
				}
				fmt.Fprintf(w, `JBL.embed_v2['jobs'] = [{id:123,url:'%s',title:'Senior Software Engineer',locations:['Zurich'],workspace:'Remote',klass:{'job-lang-en':true}}]`, url)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("Jobylon claim did not settle", result, err)
			}
			assertRichDeadlineAndLease(t, f, "jobylon")
			var reserved bool
			var gone, failures, postings int
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,consecutive_failures,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &gone, &failures, &postings); err != nil {
				t.Fatal(err)
			}
			wantCalls := 1
			if mode == "redirect" {
				wantCalls = 2
			}
			if calls != wantCalls {
				t.Fatal("request/redirect budget differs", calls)
			}
			if mode == "header" || mode == "meta" {
				if !reserved || gone != 0 || failures != 0 || postings != 1 {
					t.Fatal("reservation lost precedence")
				}
				return
			}
			if mode == "gone404" {
				if reserved || gone != 1 || failures != 0 || postings != 1 || result.Cycle.Status != "gone_pending" {
					t.Fatal("404 confirmation differs", result.Cycle)
				}
				return
			}
			if mode == "status410" || mode == "status503" || mode == "bad-item" {
				if reserved || gone != 0 || failures != 1 || postings != 1 {
					t.Fatal("failed request supplied inventory")
				}
				return
			}
			if mode == "malformed" {
				if reserved || gone != 0 || failures != 0 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("empty inventory guard differs")
				}
				return
			}
			if reserved || gone != 0 || failures != 0 {
				t.Fatal("successful inventory became failure")
			}
			var id, title string
			var locales, types []string
			var locations []int32
			var due *time.Time
			var hash *int64
			if err := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],locales,location_ids,location_types,next_scrape_at,description_r2_hash FROM job_posting WHERE source_url=$1", url).Scan(&id, &title, &locales, &locations, &types, &due, &hash); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || fmt.Sprint(locales) != "[en]" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(types) != "[remote]" || result.Batches.Inserted != map[bool]int{false: 1, true: 0}[existing] {
				t.Fatal("canonical rich fields differ", title, locales, locations, types, result)
			}
			if retained {
				var html string
				var uploaded bool
				if err := f.pg.QueryRow(ctx, "SELECT html,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid AND locale='en'", id).Scan(&html, &uploaded); err != nil || html != "<p>Delegated description</p>" || !uploaded || hash == nil || *hash != 123 {
					t.Fatal("monitor replaced retained description", err)
				}
			}
			if mode == "enrich-new" || mode == "enrich-touch" {
				if due == nil || f.r.ZScore(ctx, "ft_scrapes_simple:emp.jobylon.com", id).Val() != 0 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != url {
					t.Fatal("delegated urgent detail missing")
				}
			}
			if mode == "enrich-relist" {
				if due == nil || f.r.ZScore(ctx, "scrapes_simple:emp.jobylon.com", id).Val() != float64(due.UnixMicro())/1e6 || f.r.HGet(ctx, "scrape:"+id, "description_r2_hash").Val() != "123" {
					t.Fatal("relist deadline/hash changed")
				}
			}
			if mode == "complete" || mode == "redirect" || mode == "enrich-retained" || mode == "enrich-tombstone" {
				if due != nil || f.r.Exists(ctx, "scrape:"+id).Val() != 0 {
					t.Fatal("healthy or tombstoned rich job gained detail schedule")
				}
			}
		})
	}
}

func TestRealJSONLDDescriptionEnrichmentPreservesMonitorOwnedFields(t *testing.T) {
	f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`, "")
	ctx := context.Background()
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],locales=ARRAY['sv'],location_ids=ARRAY[2],location_types=ARRAY['remote'],employment_type='part_time' WHERE id=$1::uuid", f.original); err != nil {
		t.Fatal(err)
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, nativeJSONLDHTML) })
	result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" {
		t.Fatal("description enrichment failed", result, err)
	}
	var title, employment, html string
	var locations []int32
	var due time.Time
	var uploaded bool
	if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.employment_type,p.location_ids,d.html,d.r2_uploaded,p.next_scrape_at FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&title, &employment, &locations, &html, &uploaded, &due); err != nil {
		t.Fatal(err)
	}
	if title != "Monitor title" || employment != "part_time" || fmt.Sprint(locations) != "[2]" || !strings.Contains(html, "Salary CHF") || uploaded {
		t.Fatal("detail overwrote monitor fields or failed staging")
	}
	if score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("detail schedule/lease differs", err)
	}
}

func TestRealJSONLDDescriptionAndLocationsEnrichmentPreservesOtherMonitorFields(t *testing.T) {
	f, a, claim := independentDetailOwnedFixture(t, `{"scraper_type":"json-ld","scraper_config":{"enrich":["description","locations"]}}`, "")
	ctx := context.Background()
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],locales=ARRAY['sv'],location_ids=ARRAY[1],location_types=ARRAY['remote'],employment_type='part_time' WHERE id=$1::uuid", f.original); err != nil {
		t.Fatal(err)
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, nativeJSONLDHTML) })
	result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || result == nil || !result.Settled || result.Cycle.Status != "succeeded" {
		t.Fatal("description enrichment failed", result, err)
	}
	var title, employment, html string
	var locations []int32
	var due time.Time
	var uploaded bool
	if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],p.employment_type,p.location_ids,d.html,d.r2_uploaded,p.next_scrape_at FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&title, &employment, &locations, &html, &uploaded, &due); err != nil {
		t.Fatal(err)
	}
	if title != "Monitor title" || employment != "part_time" || fmt.Sprint(locations) != "[2]" || !strings.Contains(html, "Salary CHF") || uploaded {
		t.Fatal("detail overwrote monitor fields or failed staging")
	}
	if score, err := f.r.ZScore(ctx, "scrapes_simple:example.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("detail schedule/lease differs", err)
	}
}
