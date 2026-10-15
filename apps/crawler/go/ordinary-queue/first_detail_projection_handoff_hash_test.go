package queue

import (
	"context"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealColdDetailProjectionPreservesEarlierFallbackHash(t *testing.T) {
	for _, hash := range []string{"", "456", "-456", "9223372036854775807", "-9223372036854775808"} {
		for _, wrongLane := range []bool{false, true} {
			lane := "same-lane"
			if wrongLane {
				lane = "repair-lane"
			}
			t.Run(hash+"/"+lane, func(t *testing.T) {
				p := projectionFixture(t, Simple, `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld"}}}`)
				ctx, r := context.Background(), p.f.client.redis
				id, key := p.f.task.ID, "scrape:"+p.f.task.ID
				var before time.Time
				var canonicalHash *int64
				if e := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", id).Scan(&before, &canonicalHash); e != nil {
					t.Fatal(e)
				}
				r.HSet(ctx, key, "scrape_step", "1", "description_r2_hash", hash, "scrape_interval_hours", "12")
				r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
				queue := "ft_scrapes_simple:example.com"
				if wrongLane {
					queue = "ft_scrapes_browser:example.com"
				}
				r.ZAdd(ctx, queue, redis.Z{Score: -4, Member: id})
				if _, e := applyFirstFixture(t, p, false); e != nil {
					t.Fatal("valid historical fallback rejected", e)
				}
				if r.HGet(ctx, key, "scrape_step").Val() != "1" || r.HGet(ctx, key, "description_r2_hash").Val() != hash || r.HGet(ctx, key, "scrape_interval_hours").Val() != "12" || r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Val() != -4 || r.ZScore(ctx, "ft_scrapes_browser:example.com", id).Err() != redis.Nil {
					t.Fatal("fallback history or first priority changed")
				}
				var due time.Time
				var afterHash *int64
				if e := p.f.observer.QueryRow(ctx, "SELECT next_scrape_at,description_r2_hash FROM job_posting WHERE id=$1::uuid", id).Scan(&due, &afterHash); e != nil || !before.Equal(due) || !reflect.DeepEqual(canonicalHash, afterHash) {
					t.Fatal("canonical hash or cycle deadline changed", e)
				}
				a, claim := firstRetirementClaim(t, p)
				d, e := a.ReadWorkdayDetail(ctx, claim)
				if e != nil || d.Profile().Profile != jsonldDetailProfile || !reflect.DeepEqual(d.DescriptionHash, canonicalHash) || claim.task.Config["description_r2_hash"] != hash {
					t.Fatal("separate fallback stage or authoritative read changed", e)
				}
			})
		}
	}
}

func TestRealColdDetailProjectionRefusesMalformedFallbackHash(t *testing.T) {
	for _, hash := range []string{"garbage", "9223372036854775808", "-9223372036854775809", "00", "-0", "1.5", "1e2"} {
		t.Run(hash, func(t *testing.T) {
			p := projectionFixture(t, Simple, `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld"}}}`)
			ctx, r := context.Background(), p.f.client.redis
			id, key := p.f.task.ID, "scrape:"+p.f.task.ID
			r.HSet(ctx, key, "scrape_step", "1", "description_r2_hash", hash)
			r.ZRem(ctx, "ft_scrapes_simple:example.com", id)
			r.ZAdd(ctx, "ft_scrapes_browser:example.com", redis.Z{Score: -4, Member: id})
			if _, e := applyFirstFixture(t, p, false); e == nil || firstFixtureState(t, p) != "staged" {
				t.Fatal("invalid handoff hash adopted")
			}
			if r.HGet(ctx, key, "description_r2_hash").Val() != hash || r.HGet(ctx, key, "scrape_step").Val() != "1" || r.ZScore(ctx, "ft_scrapes_browser:example.com", id).Val() != -4 || r.ZScore(ctx, "ft_scrapes_simple:example.com", id).Err() != redis.Nil || r.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("refusal modified queue or ownership")
			}
		})
	}
}
