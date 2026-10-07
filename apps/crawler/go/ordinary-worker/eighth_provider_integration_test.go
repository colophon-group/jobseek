package worker

import (
	"context"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealEighthProvidersCanonicalWritesPolicyAndPartialSettlement(t *testing.T) {
	for _, provider := range []string{"earcu", "earcu/proxy", "cvwarehouse", "woowa/brothers", "woowa/youths", "woowa/bmart"} {
		for _, mode := range []string{"success", "reserved", "partial"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				typ := strings.Split(provider, "/")[0]
				metadata := `{"scraper_type":"skip"}`
				if provider == "earcu/proxy" {
					metadata = `{"scraper_type":"skip","proxy":true}`
				}
				f := privateRichPipelineFixtureURL(t, typ, metadata, eighthFixtureSource(provider))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				var requests atomic.Int32
				client := verifiedClaimFixtureClient(t, eighthFixtureHandler(provider, mode, &requests))
				if provider == "earcu/proxy" {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("provider settlement lost", err, result)
				}
				assertRichDeadlineAndLease(t, f, typ)
				var reserved bool
				var failures, gone int
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); err != nil {
					t.Fatal(err)
				}
				wantFailures := 0
				if mode == "partial" {
					wantFailures = 1
				}
				if reserved != (mode == "reserved") || failures != wantFailures || gone != 0 {
					t.Fatal("outcome differs", reserved, failures, gone)
				}
				if mode != "success" {
					var active bool
					var title string
					if err := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil {
						t.Fatal(err)
					}
					if result.Batches.Inserted != 0 || !active || title != "Original" {
						t.Fatal("failed or reserved inventory changed canonical rows")
					}
					return
				}
				want := 2
				if typ == "earcu" {
					want = 1
				}
				if result.Batches.Inserted != want {
					t.Fatal("native writes lost", result.Batches)
				}
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND titles[1]='Engineer'", f.board).Scan(&count); err != nil || count != want {
					t.Fatal("canonical titles differ", err, count)
				}
				if typ == "woowa" {
					variant := strings.Split(provider, "/")[1]
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_identity LIKE $2", f.board, fmt.Sprintf("woowa:%s:R-%%", variant)).Scan(&count); err != nil || count != want {
						t.Fatal("canonical source identity differs", err, count)
					}
				}
			})
		}
	}
}

func TestRealEighthEArcuProxyOwnerCannotExecuteWithDirectClient(t *testing.T) {
	f := privateRichPipelineFixtureURL(t, "earcu", `{"scraper_type":"skip","proxy":true}`, eighthFixtureSource("earcu"))
	claim, circuits := claimFixture(t, f)
	var requests atomic.Int32
	client := verifiedClaimFixtureClient(t, eighthFixtureHandler("earcu", "success", &requests))
	result, err := RunGreenhouseClaim(context.Background(), f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if err == nil || result != nil && result.Settled || requests.Load() != 0 {
		t.Fatal("proxy-owned claim acquired direct egress", err, result, requests.Load())
	}
}
