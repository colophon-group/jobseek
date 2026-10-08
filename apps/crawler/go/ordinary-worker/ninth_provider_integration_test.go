package worker

import (
	"context"
	"strings"
	"sync/atomic"
	"testing"
)

func TestRealNinthProvidersCanonicalWritesPolicyAndPartialSettlement(t *testing.T) {
	for _, provider := range []string{"beehire", "hirehive", "welcometothejungle", "computrabajo", "computrabajo/proxy", "ycombinator"} {
		for _, mode := range []string{"success", "reserved", "partial"} {
			t.Run(provider+"/"+mode, func(t *testing.T) {
				typ := strings.Split(provider, "/")[0]
				f := privateRichPipelineFixtureURL(t, typ, ninthFixtureMetadata(provider), ninthFixtureSource(provider))
				ctx := context.Background()
				claim, circuits := claimFixture(t, f)
				var requests atomic.Int32
				client := verifiedClaimFixtureClient(t, ninthFixtureHandler(provider, mode, &requests))
				if provider == "computrabajo/proxy" {
					client = credentialedProxyFixture(t, client)
				}
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("settlement lost", err, result)
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
						t.Fatal("failed inventory changed canonical content")
					}
					return
				}
				want := 2
				if typ == "computrabajo" {
					want = 21
				}
				if result.Batches.Inserted != want {
					t.Fatal("native writes differ", result.Batches)
				}
				var count int
				if typ == "computrabajo" || typ == "ycombinator" {
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND cardinality(titles)=0", f.board, f.original).Scan(&count); err != nil || count != want {
						t.Fatal("URL-only title delegation changed", err, count)
					}
					rows, err := f.pg.Query(ctx, "SELECT id::text,source_url,next_scrape_at IS NOT NULL FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original)
					if err != nil {
						t.Fatal(err)
					}
					defer rows.Close()
					for rows.Next() {
						var id, source string
						var due bool
						if err := rows.Scan(&id, &source, &due); err != nil || !due || f.r.HGet(ctx, "scrape:"+id, "source_url").Val() != source {
							t.Fatal("delegated canonical/Redis detail intent lost", err, due)
						}
					}
					if err := rows.Err(); err != nil {
						t.Fatal(err)
					}
					return
				}
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND titles[1]='Engineer'", f.board).Scan(&count); err != nil || count != want {
					t.Fatal("canonical titles differ", err, count)
				}
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting j ON j.id=d.posting_id WHERE j.board_id=$1::uuid AND d.html LIKE '%<p>Build</p>%' AND NOT d.r2_uploaded", f.board).Scan(&count); err != nil || count != want {
					t.Fatal("canonical description staging differs", err, count)
				}
				if typ == "beehire" {
					if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND 'Ingénieur'=ANY(titles) AND 'fr'=ANY(locales) AND 'en'=ANY(locales)", f.board).Scan(&count); err != nil || count != 1 {
						t.Fatal("localized canonical fields dropped", err, count)
					}
				}
			})
		}
	}
}

func TestRealNinthProxyOwnerCannotExecuteWithDirectClient(t *testing.T) {
	f := privateRichPipelineFixtureURL(t, "computrabajo", ninthFixtureMetadata("computrabajo/proxy"), ninthFixtureSource("computrabajo/proxy"))
	claim, circuits := claimFixture(t, f)
	var requests atomic.Int32
	client := verifiedClaimFixtureClient(t, ninthFixtureHandler("computrabajo", "success", &requests))
	result, err := RunGreenhouseClaim(context.Background(), f.a, claim, client, richPipelinePreparer(t, f), circuits)
	if err == nil || result != nil && result.Settled || requests.Load() != 0 {
		t.Fatal("proxy owner acquired direct egress", err, result, requests.Load())
	}
}
