package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
)

func TestRealOwnedConfiguredAPIWritesRichContentAndConservesQueue(t *testing.T) {
	realAPIRichTransportCases(t, false)
}
func TestRealProxyConfiguredAPIWritesRichContentAndConservesQueue(t *testing.T) {
	realAPIRichTransportCases(t, true)
}
func realAPIRichTransportCases(t *testing.T, proxy bool, annotations ...bool) {
	annotated := len(annotations) > 0 && annotations[0]
	for _, method := range []string{"GET", "POST"} {
		t.Run(method, func(t *testing.T) {
			metadata := map[string]any{"api_url": "https://example.com/api?page=1", "method": method, "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "name", "description": "body", "locations": "city", "employment_type": "employment", "job_location_type": "workplace", "skills": "skills", "responsibilities": "tasks"}, "scraper_type": "json-ld", "request_headers": map[string]any{"X-Required": "fixture"}, "pagination": map[string]any{"param_name": "page", "start_value": 1, "max_pages": 2}}
			if method == "POST" {
				metadata["api_url"] = "https://example.com/api"
				metadata["post_data"] = map[string]any{"page": 1}
				metadata["pagination"] = map[string]any{"param_name": "page", "start_value": 1, "max_pages": 2, "location": "body"}
			}
			if proxy {
				metadata["proxy"] = true
			}
			raw, _ := json.Marshal(metadata)
			f := privateRichPipelineFixture(t, "api_sniffer", sharedAnnotationFixtureMetadata(t, string(raw), annotated))
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			preparer := richPipelinePreparer(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != method || r.Header.Get("X-Required") != "fixture" {
					t.Fatal("declared HTTP request changed")
				}
				if method == "POST" {
					body, _ := io.ReadAll(r.Body)
					want := fmt.Sprintf(`{"page":%d}`, calls)
					if calls > 1 {
						want = fmt.Sprintf(`{"page": %d}`, calls)
					}
					if r.Header.Get("Content-Type") != "application/json" || string(body) != want {
						t.Fatal("POST pagination changed", string(body))
					}
				} else if r.URL.Query().Get("page") != fmt.Sprint(calls) {
					t.Fatal("query pagination changed")
				}
				fmt.Fprintf(w, `{"total":2,"jobs":[{"url":%q,"name":"Senior Software Engineer","body":"<p>Build useful systems.</p>","city":"Zurich","employment":"Full-time","workplace":"remote","skills":["Go","Python"],"tasks":["Salary CHF 100000-120000 yearly. 5+ years of experience."]}]}`, fmt.Sprintf("/job/%s/%d", f.company, calls))
			}))
			if proxy {
				client = credentialedProxyFixture(t, client)
			}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if err != nil || result == nil || !result.Settled || result.Batches.Inserted != 2 || calls != 2 {
				t.Fatalf("rich API did not settle: %+v %v", result, err)
			}
			var title, employment, currency, description string
			var canonicalHash *int64
			var pendingHash int64
			var locations, technologies []int32
			var locales, locationTypes []string
			var uploaded bool
			postingURL := fmt.Sprintf("https://example.com/job/%s/1", f.company)
			if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.employment_type,p.salary_currency,p.location_ids,p.technology_ids,p.locales,p.location_types,d.html,d.r2_uploaded,p.description_r2_hash,d.hash FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.source_url=$1`, postingURL).Scan(&title, &employment, &currency, &locations, &technologies, &locales, &locationTypes, &description, &uploaded, &canonicalHash, &pendingHash); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || employment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || len(locales) == 0 || fmt.Sprint(locationTypes) != "[remote]" || !strings.Contains(description, "<h3>Responsibilities</h3>") || !strings.Contains(description, "<h3>Skills</h3>") || uploaded || canonicalHash != nil || pendingHash != 6455103908091823932 {
				t.Fatal("API content/enrichment/description hash changed", title, employment, currency, locations, technologies, locales, locationTypes, description, canonicalHash, pendingHash)
			}
			if len(f.r.Keys(ctx, "ft_scrapes_*").Val()) != 0 {
				t.Fatal("rich inventory scheduled unsolicited detail work")
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
		})
	}
}

func TestRealOwnedConfiguredAPIFailurePolicyAndTotalGapProtectExistingJobs(t *testing.T) {
	realAPIFailureTransportCases(t, false)
}
func TestRealProxyConfiguredAPIFailurePolicyAndTotalGapProtectExistingJobs(t *testing.T) {
	realAPIFailureTransportCases(t, true)
}
func realAPIFailureTransportCases(t *testing.T, proxy bool, annotations ...bool) {
	annotated := len(annotations) > 0 && annotations[0]
	for _, mode := range []string{"later503", "laterMalformed", "laterReserved", "probeReserved", "totalGap", "cap", "first404"} {
		t.Run(mode, func(t *testing.T) {
			endpoint := "https://example.com/api?page=1"
			if mode == "probeReserved" {
				endpoint += "&size=1"
			}
			metadata := map[string]any{"api_url": endpoint, "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "name"}, "scraper_type": "skip", "transport_attempts": 1, "transient_403": true, "pagination": map[string]any{"param_name": "page", "start_value": 1, "max_pages": 2}}
			if mode == "cap" {
				metadata["max_items"] = 1
			}
			if proxy {
				metadata["proxy"] = true
			}
			raw, _ := json.Marshal(metadata)
			f := privateRichPipelineFixture(t, "api_sniffer", sharedAnnotationFixtureMetadata(t, string(raw), annotated))
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				calls++
				if mode == "first404" {
					w.WriteHeader(404)
					return
				}
				if calls > 1 {
					switch mode {
					case "later503":
						w.WriteHeader(503)
						return
					case "laterMalformed":
						fmt.Fprint(w, "invalid")
						return
					case "laterReserved", "probeReserved":
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "https://example.com/policy")
						w.WriteHeader(503)
						return
					}
				}
				total := 2
				if mode == "totalGap" {
					total = 10
				}
				fmt.Fprintf(w, `{"total":%d,"jobs":[{"url":%q,"name":"Engineer"}]}`, total, fmt.Sprintf("https://example.com/job/%s/%d", f.company, calls))
			}))
			if proxy {
				client = credentialedProxyFixture(t, client)
			}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatalf("API outcome not settled: %+v %v", result, err)
			}
			var reserved, active bool
			var goneChecks, failures, count int
			var evidence []byte
			if err := f.pg.QueryRow(ctx, `SELECT tdm_reserved,gone_confirmation_count,consecutive_failures,tdm_reservation,(SELECT count(*) FROM job_posting WHERE board_id=b.id) FROM job_board b WHERE id=$1::uuid`, f.board).Scan(&reserved, &goneChecks, &failures, &evidence, &count); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil {
				t.Fatal(err)
			}
			if goneChecks != 0 {
				t.Fatal("API response became provider-gone authority")
			}
			switch mode {
			case "later503", "laterMalformed":
				if result.Cycle.Status != "failed" || failures != 1 || reserved || count != 1 || !active {
					t.Fatal("partial failure wrote inventory", result.Cycle, count, active)
				}
			case "laterReserved", "probeReserved":
				if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || count != 1 || !active || calls != 2 || !strings.Contains(string(evidence), "https://example.com/policy") {
					t.Fatal("publisher policy lost later/probe binding", result.Cycle, string(evidence), calls)
				}
			case "totalGap", "cap":
				if count != 3 || !active || result.Cycle.Gone != 0 || failures != 0 {
					t.Fatal("truncated inventory disappeared existing rows", result.Cycle, count, active)
				}
			case "first404":
				if count != 1 || failures != 0 || reserved {
					t.Fatal("configured API lenient empty semantics changed", result.Cycle)
				}
			}
			assertRichDeadlineAndLease(t, f, "api_sniffer")
		})
	}
}
