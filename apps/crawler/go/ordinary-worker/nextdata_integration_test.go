package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestRealOwnedNextdataStreamRichURLDetailsDescriptionsAndFailurePrefixes(t *testing.T) {
	for _, mode := range []string{"rich", "urls", "enrich-new", "enrich-retained", "enrich-missing-locale", "later-failure", "later-reserved", "total-mismatch", "first-reserved", "meta-reserved", "wrong-title", "lenient-empty", "changed-binding", "fresh-reserved"} {
		t.Run(mode, func(t *testing.T) {
			metadata := `{"path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title","description":"body","locations":"city"},"scraper_type":"json-ld"}`
			if mode == "urls" {
				metadata = `{"path":"jobs","url_template":"https://example.com/jobs/{id}","scraper_type":"json-ld"}`
			}
			if strings.HasPrefix(mode, "enrich-") {
				metadata = strings.TrimSuffix(metadata, "}") + `,"scraper_config":{"enrich":["description"]}}`
			}
			if strings.HasPrefix(mode, "later-") {
				metadata = strings.TrimSuffix(metadata, "}") + `,"pagination":{"path":"pagination","page_count":"pages"}}`
			}
			if mode == "total-mismatch" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"pagination":{"path":"pagination","total_records":"total","page_size":1}}`
			}
			if mode == "wrong-title" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"expected_page_title":"Fixture tenant"}`
			}
			f := privateRichPipelineFixture(t, "nextdata", metadata)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			url := "https://example.com/jobs/" + f.company
			retained := mode == "enrich-retained" || mode == "enrich-missing-locale"
			if retained {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=NULL,description_r2_hash=123 WHERE id=$1::uuid", f.original, url); err != nil {
					t.Fatal(err)
				}
				locale := "en"
				if mode == "enrich-missing-locale" {
					locale = "fr"
				}
				if _, err := f.pg.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,$2,'<p>Scraped retained body</p>',123,true)", f.original, locale); err != nil {
					t.Fatal(err)
				}
			}
			claim, circuits := claimFixture(t, f)
			var calls atomic.Int64
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls.Add(1)
				if r.Method != "GET" || r.Host != "example.com" {
					t.Error("request left bound origin")
				}
				if r.URL.Query().Get("page") == "2" {
					if mode == "later-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						fmt.Fprint(w, "blocked")
						return
					}
					if mode == "later-failure" {
						w.WriteHeader(503)
						return
					}
					fmt.Fprintf(w, `<script id="__NEXT_DATA__">{"jobs":[{"id":"%s","title":"Senior Software Engineer","body":"<p>We are looking for a software engineer to build and maintain our platform. You will work with our team to deliver reliable services.</p>","city":"Zurich"}]}</script>`, f.company)
					return
				}
				switch mode {
				case "first-reserved":
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://example.com/policy")
					fmt.Fprint(w, "blocked")
					return
				case "meta-reserved":
					fmt.Fprint(w, `<meta name="tdm-reservation" content="1">`)
					return
				case "lenient-empty":
					fmt.Fprint(w, "no data")
					return
				case "changed-binding":
					if _, err := f.pg.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"url_template":"https://example.com/changed/{id}"}'::jsonb WHERE id=$1::uuid`, f.board); err != nil {
						t.Error(err)
					}
				case "fresh-reserved":
					if _, err := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); err != nil {
						t.Error(err)
					}
				}
				fmt.Fprintf(w, `<title>Different tenant</title><script id="__NEXT_DATA__">{"jobs":[{"id":"%s","title":"Senior Software Engineer","body":"<p>We are looking for a software engineer to build and maintain our platform. You will work with our team to deliver reliable services.</p>","city":"Zurich"}],"pagination":{"pages":2,"total":2}}</script>`, f.company)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if mode == "changed-binding" {
				if err == nil || result.Settled {
					t.Fatal("changed binding retained write/settle authority")
				}
				var postings int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&postings); err != nil || postings != 1 {
					t.Fatal("changed binding wrote postings", err)
				}
				return
			}
			if err != nil || result == nil || !result.Settled {
				t.Fatal("NextData claim did not settle", result, err)
			}
			assertRichDeadlineAndLease(t, f, "nextdata")
			var reserved bool
			var failures, gone, postings int
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone, &postings); err != nil {
				t.Fatal(err)
			}
			if gone != 0 {
				t.Fatal("NextData ordinary failure supplied board-gone confirmation")
			}
			if mode == "first-reserved" || mode == "meta-reserved" || mode == "fresh-reserved" {
				if !reserved || failures != 0 || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("publisher reservation changed canonical inventory")
				}
				return
			}
			if mode == "wrong-title" || mode == "lenient-empty" {
				wantFailures := 0
				if mode == "wrong-title" {
					wantFailures = 1
				}
				if reserved || failures != wantFailures || postings != 1 || result.Batches.Inserted != 0 {
					t.Fatal("empty or wrong-tenant inventory changed postings")
				}
				return
			}
			if strings.HasPrefix(mode, "later-") || mode == "total-mismatch" {
				if postings != 2 || result.Batches.Inserted != 1 || reserved != (mode == "later-reserved") || failures != map[bool]int{true: 0, false: 1}[mode == "later-reserved"] {
					t.Fatal("committed prefix or terminal outcome differs", result, postings, reserved, failures)
				}
				var active bool
				var missing int
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil || !active || missing != 3 {
					t.Fatal("incomplete stream delisted unseen posting", err)
				}
				return
			}
			var id string
			var due *time.Time
			var title *string
			var locations []int32
			if err := f.pg.QueryRow(ctx, "SELECT id::text,next_scrape_at,titles[1],location_ids FROM job_posting WHERE source_url=$1", url).Scan(&id, &due, &title, &locations); err != nil {
				t.Fatal(err)
			}
			if mode == "urls" || mode == "enrich-new" {
				if due == nil || f.r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Val() != 0 || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != url {
					t.Fatal("detail intent lost SQL/Redis identity")
				}
			}
			if mode != "urls" {
				if title == nil || *title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" {
					t.Fatal("rich canonical fields differ", title, locations)
				}
			}
			if mode == "rich" {
				if due != nil || f.r.Exists(ctx, "scrape:"+id).Val() != 0 {
					t.Fatal("complete rich monitor queued detail")
				}
			}
			if retained {
				locale := "en"
				if mode == "enrich-missing-locale" {
					locale = "fr"
				}
				var html string
				var uploaded bool
				if err := f.pg.QueryRow(ctx, "SELECT html,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid AND locale=$2", id, locale).Scan(&html, &uploaded); err != nil || html != "<p>Scraped retained body</p>" || !uploaded {
					t.Fatal("monitor teaser overwrote scraped locale", err)
				}
				if due != nil {
					t.Fatal("retained healthy body was unnecessarily queued")
				}
				if mode == "enrich-missing-locale" {
					if err := f.pg.QueryRow(ctx, "SELECT html,r2_uploaded FROM descriptions WHERE posting_id=$1::uuid AND locale='en'", id).Scan(&html, &uploaded); err != nil || html != "<p>We are looking for a software engineer to build and maintain our platform. You will work with our team to deliver reliable services.</p>" || uploaded {
						t.Fatal("missing-locale fallback was not staged", err)
					}
				}
			}
			if mode == "urls" || mode == "rich" || mode == "enrich-new" {
				var active bool
				var missing int
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil || active || missing != 4 {
					t.Fatal("NextData fragile miss threshold differs", err)
				}
			}
		})
	}
}
