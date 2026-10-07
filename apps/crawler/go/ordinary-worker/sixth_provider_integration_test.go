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
	for _, provider := range []string{"manatal", "hrmos"} {
		for _, reservedPage := range []int{1, 2} {
			t.Run(fmt.Sprintf("%s/page%d", provider, reservedPage), func(t *testing.T) {
				source, metadata := "https://www.careers-page.com/tenant", `{"scraper_type":"skip"}`
				if provider == "hrmos" {
					source, metadata = "https://hrmos.co/pages/tenant/jobs", `{"scraper_type":"json-ld"}`
				}
				f := privateRichPipelineFixtureURL(t, provider, metadata, source)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					page := 1
					if r.URL.Query().Get("page") == "2" {
						page = 2
					}
					if page == reservedPage {
						w.Header().Set("TDM-Reservation", "1")
					}
					if provider == "manatal" {
						fmt.Fprintf(w, `{"count":2,"results":[{"hash":"%d","position_name":"Engineer","description":"<p>Build</p>"}],"next":%t}`, page, page == 1)
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
			Provider, Scenario string
			Pages              map[string]string
			Expected           struct {
				Error, Truncated bool
				URLs             []string
			}
		}
	}
	if json.Unmarshal(raw, &corpus) != nil || len(corpus.Inventories) != 10 {
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
			f := privateRichPipelineFixtureURL(t, c.Provider, metadata, source)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			pages := map[string]string{}
			for source, body := range c.Pages {
				pages[normalize(source)] = body
			}
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				source := normalize("https://" + r.Host + r.URL.String())
				body, ok := pages[source]
				if !ok {
					t.Error("request left frozen scope", source)
					w.WriteHeader(500)
					return
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
			if reserved || gone != 0 {
				t.Fatal("listing invented reservation or gone", reserved, gone)
			}
			if c.Expected.Error {
				if failures != 1 || result.Batches.Inserted != 0 {
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
				if c.Provider == "hrmos" {
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
