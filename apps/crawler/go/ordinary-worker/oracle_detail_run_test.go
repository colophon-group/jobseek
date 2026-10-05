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

func TestRealOracleDetailPreservesSelectedEnrichmentAndCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"description", "description_and_employment", "configured_fields", "empty_backfill", "empty_payload", "404", "header_reserved"} {
		t.Run(mode, func(t *testing.T) {
			fields := `["description"]`
			if mode == "description_and_employment" {
				fields = `["description","employment_type"]`
			}
			metadata := `{"scraper_type":"oracle_hcm","selector":"a.job","render":true,"scraper_config":{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","enrich":` + fields + `}}`
			if mode == "configured_fields" {
				metadata = `{"scraper_type":"oracle_hcm","scraper_config":{"host":"fixture.fa.em2.oraclecloud.com","site":"CX_1","enrich":["description"],"fields":{"title":"Title","locations":"PrimaryLocation","date_posted":"ExternalPostedStartDate","description":["ExternalDescriptionStr","ExternalQualificationsStr","ExternalResponsibilitiesStr"],"valid_through":"ExternalPostedEndDate","employment_type":"JobSchedule","job_location_type":{"map":{"ORA_HYBRID":"hybrid","ORA_REMOTE":"remote","ORA_ON_SITE":"onsite"},"path":"WorkplaceTypeCode"}}}}`
			}
			source := "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123"
			f, a, claim := independentDetailOwnedFixture(t, metadata, source)
			ctx := context.Background()
			if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Monitor title'],employment_type='part_time',location_ids=ARRAY[2] WHERE id=$1::uuid", f.original); err != nil {
				t.Fatal(err)
			}
			if mode == "empty_backfill" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET titles=ARRAY[]::text[],employment_type=NULL,location_ids=ARRAY[]::integer[] WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "GET" || r.Host != "fixture.fa.em2.oraclecloud.com" || r.URL.Path != "/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails" || !strings.Contains(r.URL.RawQuery, `Id="123",siteNumber=CX_1`) {
					t.Error("Oracle request lost exact tenant/site/id", r.URL.RawQuery)
				}
				if mode == "404" {
					w.WriteHeader(404)
					return
				}
				if mode == "header_reserved" {
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "https://fixture.fa.em2.oraclecloud.com/policy")
					w.WriteHeader(503)
					return
				}
				if mode == "empty_payload" {
					fmt.Fprint(w, `{"items":[{}]}`)
					return
				}
				fmt.Fprint(w, `{"items":[{"Title":"Senior Software Engineer","PrimaryLocation":"Zurich","JobSchedule":"Full-time","WorkplaceTypeCode":"ORA_REMOTE","ExternalQualificationsStr":"<p>Qualifications</p>","ExternalResponsibilitiesStr":"<p>Responsibilities</p>","ExternalDescriptionStr":"<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"}]}`)
			})
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			wantStatus := "succeeded"
			if mode == "empty_payload" || mode == "404" {
				wantStatus = "failed"
			}
			if mode == "header_reserved" {
				wantStatus = "publisher_reserved"
			}
			if err != nil || result == nil || !result.Settled || result.Cycle.Status != wantStatus || calls != 1 {
				t.Fatal("Oracle detail did not settle expected observation", result, err, calls)
			}
			var title, employment string
			var due time.Time
			var reserved, active bool
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT titles[1],employment_type,next_scrape_at,tdm_reserved,is_active,scrape_failures FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &employment, &due, &reserved, &active, &failures); err != nil {
				t.Fatal(err)
			}
			wantTitle, wantEmployment := "Monitor title", "part_time"
			if mode == "empty_backfill" {
				wantTitle = "Senior Software Engineer"
				wantEmployment = "full_time"
			}
			if mode == "description_and_employment" {
				wantEmployment = "full_time"
			}
			wantFailures := 0
			if wantStatus == "failed" {
				wantFailures = 1
			}
			if title != wantTitle || employment != wantEmployment || !active || reserved != (mode == "header_reserved") || failures != wantFailures {
				t.Fatal("Oracle mask/failure policy replaced retained canonical fields", title, employment, reserved, active, failures)
			}
			score, err := f.r.ZScore(ctx, "scrapes_simple:"+claim.Descriptor().Domain, f.original).Result()
			if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("Oracle recurring canonical/Redis deadline differs", err)
			}
			if wantStatus == "succeeded" {
				var html string
				var uploaded bool
				var locations []int32
				if err := f.pg.QueryRow(ctx, "SELECT d.html,d.r2_uploaded,p.location_ids FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid", f.original).Scan(&html, &uploaded, &locations); err != nil || !strings.Contains(html, "Salary CHF") || uploaded || fmt.Sprint(locations) != "[2]" {
					t.Fatal("Oracle body staging/backfill differs", err)
				}
				if mode == "configured_fields" && (!strings.Contains(html, "Qualifications") || !strings.Contains(html, "Responsibilities")) {
					t.Fatal("configured Oracle description omitted provider sections")
				}
			} else if f.r.Exists(ctx, "scrape:"+f.original).Val() != 1 {
				t.Fatal("Oracle failed detail lost recurring intent")
			}
		})
	}
}
