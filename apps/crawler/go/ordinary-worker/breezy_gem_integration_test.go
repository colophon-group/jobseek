package worker

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealOwnedBreezyGemCanonicalPolicyFailureAndSettlement(t *testing.T) {
	for _, provider := range []string{"breezy", "gem"} {
		for _, mode := range []string{"success", "empty", "header", "non200_policy", "missing", "malformed", "invalid_job"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				metadata := `{"token":"fixture","scraper_type":"skip"}`
				endpoint := "https://api.gem.com/job_board/v0/fixture/job_posts/"
				threshold := 2
				if provider == "breezy" {
					metadata = `{"portal_url":"https://fixture.breezy.hr","scraper_type":"json-ld"}`
					endpoint = "https://fixture.breezy.hr/json"
					threshold = 4
				}
				f := privateRichPipelineFixture(t, provider, metadata)
				ctx := context.Background()
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET missing_count=$2 WHERE id=$1::uuid", f.original, threshold-1); err != nil {
					t.Fatal(err)
				}
				claim, circuits := claimFixture(t, f)
				postingURL := "https://example.com/job/" + f.company
				var requests atomic.Int64
				client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests.Add(1)
					if r.Method != "GET" || "https://"+r.Host+r.URL.String() != endpoint {
						t.Error("provider endpoint changed")
					}
					switch mode {
					case "header", "non200_policy":
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						if mode == "non200_policy" {
							w.WriteHeader(403)
						}
						fmt.Fprint(w, "unparseable policy body")
						return
					case "missing":
						w.WriteHeader(404)
						return
					case "malformed":
						fmt.Fprint(w, "{")
						return
					case "invalid_job":
						if provider == "gem" {
							fmt.Fprint(w, "[42]")
						} else {
							fmt.Fprint(w, `{"jobs":[]}`)
						}
						return
					case "empty":
						fmt.Fprint(w, "[]")
						return
					}
					if provider == "breezy" {
						fmt.Fprintf(w, `[{"url":%q}]`, postingURL)
						return
					}
					fmt.Fprintf(w, `[{"absolute_url":%q,"title":"Senior Software Engineer","content":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>","employment_type":"full_time","location_type":"remote","location":{"name":"Zurich"},"departments":[{"name":"Engineering"}]}]`, postingURL)
				}))
				preparer := &pipelinePreparer{}
				var selected RichPreparer = preparer
				if provider == "gem" && mode == "success" {
					selected = richPipelinePreparer(t, f)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, selected, circuits)
				if err != nil || result == nil || !result.Settled || requests.Load() != 1 {
					t.Fatal("single-request claim not settled", result, err, requests.Load())
				}
				var active, reserved bool
				var missing, failures, postings, goneChecks int
				var evidence *string
				if err := f.pg.QueryRow(ctx, "SELECT is_active,missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &missing); err != nil {
					t.Fatal(err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count,tdm_reservation::text,(SELECT count(*) FROM job_posting WHERE board_id=$1::uuid) FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &goneChecks, &evidence, &postings); err != nil {
					t.Fatal(err)
				}
				complete := mode == "success"
				policy := mode == "header" || mode == "non200_policy"
				if complete {
					want := 1
					if mode == "success" {
						want = 2
					}
					if active || missing != threshold || reserved || failures != 0 || postings != want {
						t.Fatal("complete inventory/canonical absence differs", result, active, missing, postings, failures)
					}
				} else {
					if !active || missing != threshold-1 || postings != 1 || result.Batches.Inserted != 0 {
						t.Fatal("failed/reserved inventory wrote or delisted", result)
					}
				}
				if policy {
					if !reserved || failures != 0 || evidence == nil || !strings.Contains(*evidence, endpoint) || !strings.Contains(*evidence, "https://example.com/policy") {
						t.Fatal("publisher evidence lost", evidence)
					}
				} else if !complete && mode != "empty" && (reserved || failures != 1 || goneChecks != 0) {
					t.Fatal("HTTP/inventory failure became provider gone or reservation", result.Cycle)
				}
				if mode == "empty" && (reserved || failures != 0 || goneChecks != 0) {
					t.Fatal("empty inventory bypassed existing absence guard", result.Cycle)
				}
				if provider == "breezy" && mode == "success" {
					if preparer.at != 0 || f.r.ZCard(ctx, "ft_scrapes_simple:example.com").Val() != 1 {
						t.Fatal("URL inventory did not schedule detail")
					}
				}
				if provider == "gem" && mode == "success" {
					var title, employment, currency, stored string
					var locations []int32
					var locationTypes []string
					var uploaded bool
					if err := f.pg.QueryRow(ctx, `SELECT jp.titles[1],jp.employment_type,jp.salary_currency,jp.location_ids,jp.location_types,d.html,d.r2_uploaded FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id WHERE jp.source_url=$1`, postingURL).Scan(&title, &employment, &currency, &locations, &locationTypes, &stored, &uploaded); err != nil {
						t.Fatal(err)
					}
					if title != "Senior Software Engineer" || employment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(locationTypes) != "[remote]" || !strings.Contains(stored, "100000-120000") || uploaded {
						t.Fatal("Gem canonical/preparation/description output differs", title, employment, currency, locations, locationTypes, uploaded)
					}
				}
				assertRichDeadlineAndLease(t, f, provider)
			})
		}
	}
}
