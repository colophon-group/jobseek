package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"testing"
)

func TestRealSixthProviderPublisherReservationPreventsPartialWrites(t *testing.T) {
	for _, provider := range []string{"manatal", "hrmos", "recruiterbox", "jobs_ch"} {
		for _, reservedPage := range []int{1, 2} {
			t.Run(fmt.Sprintf("%s/page%d", provider, reservedPage), func(t *testing.T) {
				source, metadata := "https://www.careers-page.com/tenant", `{"scraper_type":"skip"}`
				if provider == "hrmos" {
					source, metadata = "https://hrmos.co/pages/tenant/jobs", `{"scraper_type":"json-ld"}`
				}
				if provider == "recruiterbox" {
					source, metadata = "https://tenant.recruiterbox.com/", `{"scraper_type":"json-ld"}`
				}
				if provider == "jobs_ch" {
					source, metadata = "https://www.jobs.ch/de/firmen/123-tenant/", `{"scraper_type":"json-ld"}`
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, source)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					page := 1
					if r.URL.Query().Get("page") == "2" || r.URL.Query().Get("p") == "2" {
						page = 2
					}
					if page == reservedPage {
						w.Header().Set("TDM-Reservation", "1")
					}
					if provider == "manatal" {
						fmt.Fprintf(w, `{"count":2,"results":[{"hash":"%d","position_name":"Engineer","description":"<p>Build</p>"}],"next":%t}`, page, page == 1)
					} else if provider == "recruiterbox" {
						fmt.Fprint(w, `<script>var total_jobs: 101</script>`)
						first, last := 1, 100
						if page == 2 {
							first, last = 101, 101
						}
						for i := first; i <= last; i++ {
							fmt.Fprintf(w, `<a href="/jobs/job%d/">job</a>`, i)
						}
					} else if provider == "jobs_ch" {
						rows := []any{}
						first, last := 1, 100
						if page == 2 {
							first, last = 101, 101
						}
						for i := first; i <= last; i++ {
							rows = append(rows, map[string]any{"id": fmt.Sprintf("00000000-0000-0000-0000-%012d", i), "company": map[string]any{"id": "123"}})
						}
						json.NewEncoder(w).Encode(map[string]any{"documents": rows, "numPages": 2, "currentPage": page, "totalHits": 101, "rows": 100, "start": (page - 1) * 100})
					} else {
						fmt.Fprintf(w, `<div id="jsi-joblist">全 2 件中 1 件</div><span class="current">%d</span><a href="/pages/tenant/jobs/%d">job</a>`, page, page)
					}
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled || result.Batches.Inserted != 0 {
					t.Fatal("publisher result failed settlement", result, e)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var reserved, active bool
				var failures int
				var title string
				if e := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures); e != nil {
					t.Fatal(e)
				}
				if e := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil {
					t.Fatal(e)
				}
				if !reserved || failures != 0 || !active || title != "Original" {
					t.Fatal("reservation changed canonical content", reserved, failures, active, title)
				}
			})
		}
	}
}

func TestRealSixthProviderReferencesCommitCanonicalSettlement(t *testing.T) {
	raw, e := os.ReadFile("../api-sniffer-monitor/testdata/python_sixth_provider_core.json")
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		Inventories []struct {
			Provider, Scenario, Source string
			Metadata                   map[string]any
			Pages                      map[string]string
			Statuses                   map[string]int
			Expected                   struct {
				Error, Truncated, Gone bool
				URLs                   []string
			}
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.Inventories) != 33 {
		t.Fatal("reference inventory corpus unavailable")
	}
	normalize := func(source string) string {
		u, e := url.Parse(source)
		if e != nil {
			t.Fatal(e)
		}
		u.RawQuery = u.Query().Encode()
		return u.String()
	}
	for _, c := range corpus.Inventories {
		t.Run(c.Provider+"/"+c.Scenario, func(t *testing.T) {
			source, metadata := "https://www.careers-page.com/tenant", `{"scraper_type":"skip"}`
			if c.Provider == "hrmos" {
				source, metadata = "https://hrmos.co/pages/tenant/jobs", `{"scraper_type":"json-ld"}`
			}
			if c.Provider == "recruiterbox" {
				source, metadata = "https://tenant.recruiterbox.com/", `{"scraper_type":"json-ld"}`
			}
			if c.Provider == "jobs_ch" {
				source = c.Source
				md := map[string]any{"scraper_type": "json-ld"}
				for key, value := range c.Metadata {
					md[key] = value
				}
				raw, _ := json.Marshal(md)
				metadata = string(raw)
			}
			f := privateRichPipelineFixtureURL(t, c.Provider, metadata, source)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			pages := map[string]string{}
			for source, body := range c.Pages {
				pages[normalize(source)] = body
			}
			statuses := map[string]int{}
			for source, status := range c.Statuses {
				statuses[normalize(source)] = status
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := normalize("https://" + r.Host + r.URL.String())
				body, ok := pages[source]
				if !ok {
					t.Error("request left frozen scope", source)
					w.WriteHeader(500)
					return
				}
				if status := statuses[source]; status != 0 {
					w.WriteHeader(status)
				}
				fmt.Fprint(w, body)
			}))
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if e != nil || result == nil || !result.Settled {
				t.Fatal("native provider failed settlement", result, e)
			}
			assertRichDeadlineAndLease(t, f, c.Provider)
			var failures, gone int
			var reserved bool
			if e := f.pg.QueryRow(ctx, "SELECT consecutive_failures,gone_confirmation_count,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &gone, &reserved); e != nil {
				t.Fatal(e)
			}
			wantGone := 0
			if c.Expected.Gone {
				wantGone = 1
			}
			if reserved || gone != wantGone {
				t.Fatal("listing invented reservation or gone", reserved, gone)
			}
			if c.Expected.Error || c.Expected.Truncated {
				var active bool
				var title string
				if e := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil {
					t.Fatal(e)
				}
				if !active || title != "Original" {
					t.Fatal("failed/incomplete listing changed existing canonical posting", active, title)
				}
			}
			if c.Expected.Error {
				wantFailures := 1
				if c.Expected.Gone {
					wantFailures = 0
				}
				if failures != wantFailures || result.Batches.Inserted != 0 {
					t.Fatal("failed listing wrote content", failures, result)
				}
				return
			}
			if failures != 0 {
				t.Fatal("valid listing failed", failures)
			}
			for _, source := range c.Expected.URLs {
				var id string
				var title *string
				var due bool
				if e := f.pg.QueryRow(ctx, "SELECT id::text,titles[1],next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND source_url=$2", f.board, source).Scan(&id, &title, &due); e != nil {
					t.Fatal(e)
				}
				if c.Provider != "manatal" {
					if title != nil || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
						t.Fatal("URL-only detail intent lost")
					}
				}
				if c.Provider == "manatal" && (title == nil || (*title != "one" && *title != "two") || due) {
					t.Fatal("rich content/detail scheduling differs", title, due)
				}
			}
		})
	}
}
