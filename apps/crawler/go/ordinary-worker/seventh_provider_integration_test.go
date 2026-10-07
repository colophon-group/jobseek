package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

func TestRealSeventhProvidersCanonicalWritesAndPublisherSettlement(t *testing.T) {
	for _, provider := range []string{"deel", "hibob", "traffit"} {
		for _, reserved := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/reserved=%t", provider, reserved), func(t *testing.T) {
				source := "https://tenant.traffit.com/career/"
				if provider == "deel" {
					source = "https://jobs.deel.com/tenant"
				}
				if provider == "hibob" {
					source = "https://tenant.careers.hibob.com/"
				}
				f := privateRichPipelineFixtureURL(t, provider, `{"scraper_type":"skip"}`, source)
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				requests := 0
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					if provider == "deel" && strings.HasSuffix(r.URL.Path, "career_page_settings") {
						fmt.Fprint(w, `{"organizationId":"org","jobBoard":{"id":"board"}}`)
						return
					}
					if reserved && (provider != "traffit" || requests == 2) {
						w.Header().Set("TDM-Reservation", "1")
					}
					switch provider {
					case "deel":
						fmt.Fprint(w, `[{"id":"one","title":"Engineer","richtextDescription":"<p>Build</p>","job":{"jobLocations":[{"location":{"name":"Zürich"}}]}}]`)
					case "hibob":
						fmt.Fprint(w, `{"jobAdDetails":[{"id":"one","title":"Engineer","description":"<p>Build</p>","site":"Zürich","workspaceType":"Hybrid"}]}`)
					case "traffit":
						w.Header().Set("X-Result-Total-Pages", "2")
						fmt.Fprintf(w, `[{"url":"https://tenant.traffit.com/job/%d","advert":{"name":"Engineer","values":[{"field_id":"description","value":"<p>Build</p>"}]}}]`, requests)
					}
				}))
				result, e := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if e != nil || result == nil || !result.Settled {
					t.Fatal("provider failed settlement", e, result)
				}
				assertRichDeadlineAndLease(t, f, provider)
				var actualReserved bool
				var failures, gone int
				if e := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&actualReserved, &failures, &gone); e != nil {
					t.Fatal(e)
				}
				if actualReserved != reserved || failures != 0 || gone != 0 {
					t.Fatal("provider state differs", actualReserved, failures, gone)
				}
				if reserved {
					var active bool
					var title string
					if e := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); e != nil {
						t.Fatal(e)
					}
					if result.Batches.Inserted != 0 || !active || title != "Original" {
						t.Fatal("partial or reserved listing changed content")
					}
					return
				}
				want := 1
				if provider == "traffit" {
					want = 2
				}
				if result.Batches.Inserted != want {
					t.Fatal("native rich writes lost", result.Batches)
				}
				var count int
				if e := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND titles[1]='Engineer'", f.board).Scan(&count); e != nil {
					t.Fatal(e)
				}
				if count != want {
					t.Fatal("canonical titles differ", count)
				}
			})
		}
	}
}
