package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealBeisenModernLegacyCanonicalMasksPolicyGoneAndIncompleteInventories(t *testing.T) {
	for _, mode := range []string{"modern", "legacy-new", "legacy-retained", "legacy-relisted", "root-reserved", "root-meta", "api-reserved", "disabled", "root-404", "root-410", "api-404", "legacy-first-gone", "legacy-later-failure", "incomplete", "changed-binding"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"scraper_type":"skip"}`
			legacy := strings.HasPrefix(mode, "legacy-")
			if legacy {
				metadata = `{"tenant":"fixture","variant":"legacy","listing_path":"/Social","legacy_template":"standard","scraper_type":"dom","scraper_config":{"enrich":["description"],"steps":[{"tag":"p","field":"description","html":true}]}}`
			}
			f := privateRichPipelineFixture(t, "beisen", metadata)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid`, f.original); err != nil {
				t.Fatal(err)
			}
			publicID := f.company[:14] + "4" + f.company[15:19] + "8" + f.company[20:]
			url := "https://fixture.zhiye.com/social/detail?jobAdId=" + publicID
			if legacy {
				url = "https://fixture.zhiye.com/zpdetail/123"
			}
			retained := mode == "legacy-retained" || mode == "legacy-relisted"
			if retained {
				if _, err := f.pg.Exec(ctx, `UPDATE job_posting SET source_url=$2,titles=ARRAY['Fully scraped retained title'],location_ids=ARRAY[1],description_r2_hash=123,next_scrape_at=NULL,missing_count=0,is_active=NOT $3 WHERE id=$1::uuid`, f.original, url, mode == "legacy-relisted"); err != nil {
					t.Fatal(err)
				}
				if _, err := f.pg.Exec(ctx, `INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded)VALUES($1::uuid,'en','<p>Fully scraped retained body</p>',123,true)`, f.original); err != nil {
					t.Fatal(err)
				}
			}
			claim, circuits := claimFixture(t, f)
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/" {
					if mode == "root-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						fmt.Fprint(w, "bad")
						return
					}
					if mode == "root-meta" {
						fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
						return
					}
					if mode == "root-404" {
						w.WriteHeader(404)
						return
					}
					if mode == "root-410" {
						w.WriteHeader(410)
						return
					}
					if legacy {
						fmt.Fprint(w, "legacy root")
						return
					}
					status := 1
					if mode == "disabled" {
						status = 0
					}
					fmt.Fprintf(w, `<script>var BSGlobal = {"PortalId":"22222222-2222-4222-8222-222222222222","tenantInfo":{"Id":123,"Status":%d}};</script>`, status)
					return
				}
				if r.URL.Path == "/api/Jobad/GetJobAdPageList" {
					if r.Method != "POST" {
						t.Error("modern listing used wrong method")
					}
					if mode == "api-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						fmt.Fprint(w, "bad")
						return
					}
					if mode == "api-404" {
						w.WriteHeader(404)
						return
					}
					if mode == "changed-binding" {
						if _, err := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata||'{"tenant_id":124}'::jsonb WHERE id=$1::uuid`, f.board); err != nil {
							t.Fatal(err)
						}
					}
					count := 1
					if mode == "incomplete" {
						count = 2
					}
					data := map[string]any{"Code": 200, "Count": count, "Data": []any{map[string]any{"Id": publicID, "CategoryId": "1", "JobAdName": "Senior Software Engineer", "Duty": "Build and maintain a reliable software platform with our engineering team.", "Require": "Relevant engineering experience.", "LocNames": []string{"Zurich"}, "Status": 1}}}
					body, _ := json.Marshal(data)
					w.Write(body)
					return
				}
				if r.URL.Path == "/Social" {
					if mode == "legacy-first-gone" {
						w.WriteHeader(410)
						return
					}
					if r.URL.Query().Get("PageIndex") == "2" {
						w.WriteHeader(404)
						return
					}
					fmt.Fprint(w, `<script>_splash('new_zhiye_com')</script><table><tr><td><a href="/zpdetail/123" title="Senior Software Engineer">Engineer</a></td><td>Zurich</td><td>2026-10-01</td></tr></table>`)
					if mode == "legacy-later-failure" {
						fmt.Fprint(w, `<a href="?PageIndex=2">Next</a>`)
					}
					return
				}
				t.Error("unbound request")
				w.WriteHeader(500)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if mode == "changed-binding" {
				if err == nil || result.Settled {
					t.Fatal("changed binding kept write authority")
				}
				return
			}
			if err != nil || result == nil || !result.Settled {
				t.Fatal(result, err)
			}
			assertRichDeadlineAndLease(t, f, "beisen")
			var reserved bool
			var failures int
			var status string
			if err := f.pg.QueryRow(ctx, `SELECT tdm_reserved,consecutive_failures,board_status FROM job_board WHERE id=$1::uuid`, f.board).Scan(&reserved, &failures, &status); err != nil {
				t.Fatal(err)
			}
			if mode == "root-reserved" || mode == "root-meta" || mode == "api-reserved" {
				if !reserved || result.Batches.Inserted != 0 {
					t.Fatal("publisher reservation wrote inventory")
				}
				return
			}
			if mode == "disabled" || mode == "root-404" || mode == "root-410" || mode == "legacy-first-gone" {
				if status != "gone_pending" || failures != 0 || result.Batches.Inserted != 0 {
					t.Fatal("provider gone handling changed", status, failures)
				}
				var code *int
				if err := f.pg.QueryRow(ctx, `SELECT last_gone_status FROM job_board WHERE id=$1::uuid`, f.board).Scan(&code); err != nil || code != nil {
					t.Fatal("legacy BoardGoneError status changed", err)
				}
				return
			}
			if mode == "api-404" || mode == "legacy-later-failure" {
				if failures != 1 || result.Batches.Inserted != 0 || status == "gone_pending" {
					t.Fatal("page failure supplied inventory or board disappearance")
				}
				return
			}
			if mode == "incomplete" {
				if result.Cycle.GoneSkipped != "truncated" || result.Batches.Inserted != 1 {
					t.Fatal("incomplete page authorized disappearance")
				}
				return
			}
			if retained {
				var title, body string
				var hash int64
				var locations []int64
				if err := f.pg.QueryRow(ctx, `SELECT titles[1],location_ids,description_r2_hash FROM job_posting WHERE id=$1::uuid`, f.original).Scan(&title, &locations, &hash); err != nil || title != "Fully scraped retained title" || len(locations) != 1 || locations[0] != 1 || hash != 123 {
					t.Fatal("partial legacy inventory overwrote scraped fields", err)
				}
				if err := f.pg.QueryRow(ctx, `SELECT html FROM descriptions WHERE posting_id=$1::uuid AND locale='en'`, f.original).Scan(&body); err != nil || body != "<p>Fully scraped retained body</p>" {
					t.Fatal("legacy body overwritten", err)
				}
			} else {
				if result.Batches.Inserted != 1 {
					t.Fatal("canonical rich insert lost")
				}
				var title string
				if err := f.pg.QueryRow(ctx, `SELECT titles[1]FROM job_posting WHERE source_url=$1`, url).Scan(&title); err != nil || title != "Senior Software Engineer" {
					t.Fatal("canonical title lost", err)
				}
			}
			if mode == "legacy-new" || mode == "legacy-relisted" {
				var id string
				if err := f.pg.QueryRow(ctx, `SELECT id::text FROM job_posting WHERE source_url=$1`, url).Scan(&id); err != nil {
					t.Fatal(err)
				}
				queueName := "ft_scrapes_simple:fixture.zhiye.com"
				if mode == "legacy-relisted" {
					queueName = "scrapes_simple:fixture.zhiye.com"
				}
				if f.r.ZScore(ctx, queueName, id).Err() != nil {
					t.Fatal("legacy description enrichment lost detail intent")
				}
			}
		})
	}
}
