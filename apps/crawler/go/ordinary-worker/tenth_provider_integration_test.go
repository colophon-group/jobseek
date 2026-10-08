package worker

import (
	"context"
	"sync/atomic"
	"testing"
)

func tenthFixtureMetadata(provider string) string {
	if provider == "typify" {
		return `{"scraper_type":"json-ld","scraper_config":{"enrich":["description"]}}`
	}
	if provider == "intervieweb" {
		return `{"scraper_type":"json-ld"}`
	}
	return `{"scraper_type":"skip"}`
}

func TestRealTenthProvidersCanonicalFieldsIdentityDetailIntentAndFailedInventorySettlement(t *testing.T) {
	for _, provider := range []string{"intervieweb", "typify", "universia", "talentreef"} {
		for _, mode := range []string{"complete", "reserved", "partial"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				f := privateRichPipelineFixtureURL(t, provider, tenthFixtureMetadata(provider), tenthFixtureSource(provider))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				var calls atomic.Int32
				client := verifiedClaimFixtureClient(t, tenthFixtureHandler(provider, mode, &calls))
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("real queue settlement failed", err, result)
				}
				assertRichDeadlineAndLease(t, f, provider)
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
					t.Fatal("terminal outcome differs", reserved, failures, gone)
				}
				if mode != "complete" {
					var active bool
					var title string
					if err := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil || !active || title != "Original" || result.Batches.Inserted != 0 {
						t.Fatal("unproved inventory changed canonical postings", err, result.Batches)
					}
					return
				}
				want := 1
				if provider == "intervieweb" {
					want = 2
				}
				if result.Batches.Inserted != want {
					t.Fatal("inserted inventory differs", result.Batches)
				}
				if provider == "intervieweb" || provider == "typify" {
					rows, err := f.pg.Query(ctx, "SELECT id::text,source_url,next_scrape_at IS NOT NULL,coalesce(titles[1],'') FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original)
					if err != nil {
						t.Fatal(err)
					}
					defer rows.Close()
					count := 0
					for rows.Next() {
						count++
						var id, source, title string
						var due bool
						if err := rows.Scan(&id, &source, &due, &title); err != nil || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
							t.Fatal("canonical and Redis detail intent diverged", err, due)
						}
						if provider == "intervieweb" && title != "" || provider == "typify" && title != "Engineer" {
							t.Fatal("detail title mask changed", title)
						}
					}
					if rows.Err() != nil || count != want {
						t.Fatal("detail rows incomplete")
					}
					return
				}
				var count int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting p ON p.id=d.posting_id WHERE p.board_id=$1::uuid AND p.titles[1]='Engineer' AND d.html='<p>Build Go.</p>' AND NOT d.r2_uploaded", f.board).Scan(&count); err != nil || count != 1 {
					t.Fatal("rich canonical fields and R2 intent differ", err, count)
				}
				var identity string
				if err := f.pg.QueryRow(ctx, "SELECT source_identity FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&identity); err != nil {
					t.Fatal(err)
				}
				wantIdentity := "talentreef:123:17"
				if provider == "universia" {
					wantIdentity = "universia:11111111-1111-4111-8111-111111111111:22222222-2222-4222-8222-222222222222"
				}
				if identity != wantIdentity {
					t.Fatal("durable provider identity lost", identity)
				}
			})
		}
	}
}
