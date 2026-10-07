package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealRSSDOMSharedPolicyPreservesAcceptedWritesAndRejectsTerminalAbsence(t *testing.T) {
	for _, family := range []string{"generic", "successfactors", "teamtailor", "dom-rich", "dom-urls"} {
		for _, mode := range []string{"accepted", "boundary-mixed", "boundary-all", "classification", "job-exclusion"} {
			t.Run(family+"/"+mode, func(t *testing.T) {
				provider := "rss"
				md := `"preset":"` + family + `","feed_url":"https://example.com/feed"`
				if family == "successfactors" {
					md = `"preset":"successfactors","feed_url":"https://example.com/googlefeed.xml"`
				}
				if family == "teamtailor" {
					md = `"preset":"teamtailor","feed_url":"https://example.com/jobs.rss"`
				}
				if strings.HasPrefix(family, "dom-") {
					provider = "dom"
					md = `"url_filter":"/jobs/"`
					if family == "dom-rich" {
						md += `,"rich_rows":{"row_selector":"article","link_selector":"a","description_selector":".description","default_locations":["Zurich"]}`
					}
				}
				md = `{` + md + `,"scraper_type":"skip","url_allowlist":"https://example\\.com/jobs/[a-z0-9-]+","job_filter":{"field":"title","include":"Engineer","exclude":"Intern","require_classification":true}}`
				f := privateRichPipelineFixture(t, provider, md)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				claim, circuits := claimFixture(t, f)
				accepted := "https://example.com/jobs/" + f.company
				outside := "https://foreign.example/jobs/" + f.company
				urls := []string{accepted}
				if mode == "boundary-mixed" {
					urls = append(urls, outside)
				}
				if mode == "boundary-all" {
					urls = []string{outside}
				}
				title := "Software Engineer"
				if mode == "classification" {
					title = "Unknown occupation"
				}
				if mode == "job-exclusion" {
					title = "Intern"
				}
				var body strings.Builder
				if provider == "rss" {
					body.WriteString(`<rss xmlns:g="http://base.google.com/ns/1.0" xmlns:tt="https://teamtailor.com/locations"><channel>`)
				}
				for _, source := range urls {
					if provider == "dom" {
						fmt.Fprintf(&body, `<article><a href="%s">%s</a><div class="description"><p>Build reliable systems.</p></div></article>`, source, title)
					} else {
						fmt.Fprintf(&body, `<item><link>%s</link><title>%s</title><description><![CDATA[<p>Build reliable systems.</p>]]></description><g:title>%s</g:title><g:description><![CDATA[<p>Build reliable systems.</p>]]></g:description><g:location>Zurich</g:location></item>`, source, title, title)
					}
				}
				if provider == "rss" {
					body.WriteString(`</channel></rss>`)
				}
				client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) { fmt.Fprint(w, body.String()) })
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("shared-policy cycle did not settle", result, err)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var active bool
				var missing, failures, empty, gone int
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,empty_check_count,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &empty, &gone); err != nil {
					t.Fatal(err)
				}
				urlOnly := family == "dom-urls"
				failed := strings.HasPrefix(mode, "boundary-") || mode == "classification" && !urlOnly
				if failed {
					if result.Cycle.Status != "failed" || failures != 1 || empty != 0 || gone != 0 || !active || missing != 3 {
						t.Fatal("policy failure advanced terminal absence state", result, failures, empty, gone, active, missing)
					}
				} else if result.Cycle.Status != "succeeded" || failures != 0 {
					t.Fatal("reviewed policy rejected successful cycle", result, failures)
				}
				inserted := 0
				if mode == "accepted" || mode == "boundary-mixed" || urlOnly && (mode == "classification" || mode == "job-exclusion") {
					inserted = 1
				}
				if result.Batches.Inserted != inserted {
					t.Fatal("accepted prefix/content filtering changed", result.Batches, inserted)
				}
				var foreign int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE source_url=$1", outside).Scan(&foreign); err != nil || foreign != 0 {
					t.Fatal("rejected provider URL persisted", err)
				}
				if inserted == 1 {
					var posting string
					if err := f.pg.QueryRow(ctx, "SELECT id::text FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, accepted).Scan(&posting); err != nil {
						t.Fatal(err)
					}
					if urlOnly {
						if f.r.HGet(ctx, "scrape:"+posting, "source_url").Val() != accepted {
							t.Fatal("URL-only accepted prefix lost independent details")
						}
					} else {
						var savedTitle, html string
						if err := f.pg.QueryRow(ctx, "SELECT p.titles[1],d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", posting).Scan(&savedTitle, &html); err != nil || savedTitle != title || html != "<p>Build reliable systems.</p>" && !strings.Contains(html, "<p>Build reliable systems.</p>") {
							t.Fatal("accepted prefix lost canonical title/description", savedTitle, html, err)
						}
					}
				}
				// Legacy _record_monitor_host_outcome accounts once per failed
				// monitor, including body/config failures after a HTTP 200.
				if failed {
					if count, err := f.r.Get(ctx, "host_fail:example.com").Int(); err != nil || count != 1 {
						t.Fatal("failed monitor changed shared circuit accounting", count, err)
					}
				} else if f.r.Exists(ctx, "host_fail:example.com").Val() != 0 {
					t.Fatal("successful monitor retained a host failure")
				}

			})
		}
	}
}

func TestRealRSSSharedPolicyLateClassificationKeepsEarlierStreamBatch(t *testing.T) {
	md := `{"preset":"generic","feed_url":"https://example.com/feed","scraper_type":"skip","job_filter":{"field":"title","include":"Engineer","exclude":"Intern","require_classification":true}}`
	f := privateRichPipelineFixture(t, "rss", md)
	ctx := context.Background()
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=3 WHERE id=$1::uuid", f.original); err != nil {
		t.Fatal(err)
	}
	claim, circuits := claimFixture(t, f)
	client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprint(w, `<rss><channel>`)
		for n := 0; n < 201; n++ {
			title := "Software Engineer"
			if n == 200 {
				title = "Unclassified"
			}
			fmt.Fprintf(w, `<item><link>https://example.com/jobs/%s/%d</link><title>%s</title><description><![CDATA[<p>Build reliable systems.</p>]]></description></item>`, f.company, n, title)
		}
		fmt.Fprint(w, `</channel></rss>`)
	})
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if err != nil || result == nil || !result.Settled || result.Cycle.Status != "failed" || result.Batches.Inserted != 200 {
		t.Fatal("late classification lost committed original RSS batch", result, err)
	}
	var count int
	var active bool
	var missing int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil || count != 201 {
		t.Fatal("RSS committed prefix count changed", count, err)
	}
	if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil || !active || missing != 3 {
		t.Fatal("failed stream delisted an existing posting", err)
	}
	assertRichDeadlineAndLease(t, f, "rss")
}
