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

func TestRealFirstRetirementAfterLegacyUKGListingDiscovery(t *testing.T) {
	for _, mode := range []string{"learned-listing", "learned-identifiers", "changed-parser", "changed-board", "cache-only", "foreign-listing", "configured-listing-changed"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			listing := "https://recruiting.ultipro.com/ABC123/JobBoard/22222222-2222-4222-8222-222222222222"
			metadata := `{"scraper_type":"embedded","scraper_config":{"variable":"job","path":"job","fields":{"title":"title","description":"description"}}}`
			if mode == "configured-listing-changed" {
				metadata = strings.TrimSuffix(metadata, "}") + `,"listing_url":"https://recruiting.ultipro.com/OLD123/JobBoard/22222222-2222-4222-8222-222222222222"}`
			}
			p := firstIndependentDetailFixture(t, metadata, "https://jobs.example.net/job/42", "jobs.example.net")
			board := p.plan.document.Details[0].BoardID
			if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type='ukg',board_url=$2 WHERE id=$1::uuid", board, listing); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.HSet(ctx, "board:"+board, "crawler_type", "ukg", "board_url", listing).Err(); err != nil {
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
			additions := `,"listing_url":"` + listing + `"`
			if mode == "learned-identifiers" {
				additions += `,"host":"recruiting.ultipro.com","tenant":"ABC123","board_id":"22222222-2222-4222-8222-222222222222"`
			}
			changed := strings.TrimSuffix(metadata, "}") + additions + `}`
			if mode == "configured-listing-changed" {
				changed = strings.Replace(metadata, "OLD123", "ABC123", 1)
			}
			if mode == "foreign-listing" {
				changed = strings.Replace(changed, "recruiting.ultipro.com/ABC123", "foreign.example.com/ABC123", 1)
			}
			if mode == "changed-parser" {
				changed = strings.Replace(changed, `"description":"description"`, `"description":"other"`, 1)
			}
			if mode == "changed-board" {
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2 WHERE id=$1::uuid", board, listing+"/other"); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.HSet(ctx, "board:"+board, "board_url", listing+"/other").Err(); err != nil {
					t.Fatal(err)
				}
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
			if mode != "learned-listing" && mode != "learned-identifiers" {
				if !errors.Is(err, ErrAuthorityLost) || firstFixtureState(t, p) != "active" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
					t.Fatal("unrelated configuration change admitted or partially retired", err)
				}
				return
			}
			if err != nil || result.State != "retired" || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("learned listing prevented cold reversal or changed canonical data", err)
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
