package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealFirstRetirementAfterLegacyAvaturePortalDiscovery(t *testing.T) {
	realAvatureRetirementTransportCases(t, false)
}
func TestRealFirstProxyRetirementAfterLegacyAvaturePortalDiscovery(t *testing.T) {
	realAvatureRetirementTransportCases(t, true)
}
func realAvatureRetirementTransportCases(t *testing.T, proxy bool) {
	for _, mode := range []string{"learned-portal", "changed-parser", "changed-listing", "cache-only", "invalid-portal", "configured-portal-changed"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			metadata := `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}]},"listing_url":"https://careers.example.com/jobs"}`
			if mode == "configured-portal-changed" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"portal_id":"22"}`
			}
			if proxy {
				metadata = proxyDetailMetadata(t, metadata, true)
			}
			p := firstIndependentDetailFixture(t, metadata, "https://jobs.example.net/job/42", "jobs.example.net")
			board := p.plan.document.Details[0].BoardID
			if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type='avature' WHERE id=$1::uuid", board); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.HSet(ctx, "board:"+board, "crawler_type", "avature").Err(); err != nil {
				t.Fatal(err)
			}
			plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, []string{board})
			if err != nil {
				t.Fatal(err)
			}
			p.plan = plan
			t.Cleanup(func() {
				_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
			})
			a, claim := firstRetirementClaim(t, p)
			portal := `"23"`
			if mode == "invalid-portal" {
				portal = `"foreign"`
			}
			changed := strings.TrimSuffix(metadata, "}") + `,"portal_id":` + portal + `}`
			if mode == "configured-portal-changed" {
				changed = strings.Replace(metadata, `"portal_id":"22"`, `"portal_id":"23"`, 1)
			}
			if mode == "changed-parser" {
				changed = strings.Replace(changed, `"tag":"h1"`, `"tag":"h2"`, 1)
			}
			if mode == "changed-listing" {
				changed = strings.Replace(changed, "https://careers.example.com/jobs", "https://careers.example.com/other", 1)
			}
			if mode != "cache-only" {
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", board, changed); err != nil {
					t.Fatal(err)
				}
			}
			if err := p.f.client.redis.HSet(ctx, "board:"+board, "metadata", changed).Err(); err != nil {
				t.Fatal(err)
			}
			canonical, before := coldCanonicalSnapshot(t, p.f), snapshot(t, p.f.client)
			if _, err := a.ReadWorkdayDetail(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("changed configuration granted runtime detail authority", err)
			}
			result, err := applyFirstFixture(t, p, true)
			if mode != "learned-portal" {
				if !errors.Is(err, ErrAuthorityLost) || firstFixtureState(t, p) != "active" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
					t.Fatal("unrelated configuration change admitted or partially retired", err)
				}
				return
			}
			if err != nil || result.State != "retired" || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("learned portal prevented cold reversal or changed canonical data", err)
			}
			var due time.Time
			if err := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", claim.task.ID).Scan(&due); err != nil {
				t.Fatal(err)
			}
			score, err := p.f.client.redis.ZScore(ctx, "scrapes_simple:"+claim.task.Domain, claim.task.ID).Result()
			if err != nil || score != seconds(due) || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 || p.f.client.redis.ZScore(ctx, "inflight:simple", inflight(p.f.task)).Err() != redis.Nil || p.f.client.redis.HExists(ctx, "inflight_tokens:simple", inflight(p.f.task)).Val() {
				t.Fatal("cold reversal lost canonical schedule or retained ownership", err)
			}
			if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retired attempt retained authority", err)
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			if p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("retirement was not durable")
			}
			firstFixtureSave(t, p, false)
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("completed cold reversal repeated publication", err)
			}
		})
	}
}
