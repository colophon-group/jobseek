package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealStagedOverlappingMonitorCancellationPreservesB0(t *testing.T) {
	for _, mode := range []string{"clean", "legacy-inflight", "changed-b0", "foreign-projection"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			ctx := context.Background()
			id := p.target.document.Boards[0].ID
			metadata := `{"render":false,"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}]}}`
			if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type='dom',throttle_key='jobs.example.test',metadata=$2::jsonb WHERE id=$1::uuid", id, metadata); e != nil {
				t.Fatal(e)
			}
			if e := p.f.client.redis.HSet(ctx, "board:"+id, "crawler_type", "dom", "throttle_key", "jobs.example.test", "metadata", metadata).Err(); e != nil {
				t.Fatal(e)
			}
			target, e := CaptureColdB0Target(ctx, p.f.observer, p.f.client, p.f.epoch, p.target.document.Namespace, p.target.document.ShardID, p.target.document.Cohort, []byte(p.target.lua))
			if e != nil {
				t.Fatal(e)
			}
			p.target = target
			p.plan, e = p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{id}, nil)
			if e != nil {
				t.Fatal(e)
			}
			seedPublicationB0(t, p.f.client, p.target, p.f.epoch)
			if mode == "legacy-inflight" {
				for _, worker := range []string{"simple", "browser"} {
					if e := p.f.client.redis.ZAdd(ctx, "inflight:"+worker, redis.Z{Score: 1, Member: "monitor|jobs.example.test|" + id}).Err(); e != nil {
						t.Fatal(e)
					}
				}
			}
			if _, e := applyFirstFixture(t, p, false); !errors.Is(e, ErrAuthorityLost) {
				t.Fatal("B0 overlap activated", e)
			}
			if mode == "changed-b0" {
				p.f.client.redis.HSet(ctx, "board:"+id, "metadata", "{}")
			}
			if mode == "foreign-projection" {
				p.f.client.redis.Set(ctx, ownershipProjectionKey, "{}", 0)
			}
			before := snapshot(t, p.f.client)
			canonical := coldCanonicalSnapshot(t, p.f)
			result, e := applyFirstFixture(t, p, true)
			if mode == "clean" || mode == "legacy-inflight" {
				if e != nil || result.State != "staged" {
					t.Fatal("inert overlap could not cancel", e)
				}
			} else if e == nil {
				t.Fatal("changed authority accepted")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
				t.Fatal("cancellation changed B0, canonical data or staged history")
			}
			if mode == "clean" || mode == "legacy-inflight" {
				if _, e := applyFirstFixture(t, p, false); e == nil {
					t.Fatal("cancelled overlap gained activation authority")
				}
			}
		})
	}
}

func TestRealStagedCancellationPreservesLegacyInflight(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		for _, published := range []bool{false, true} {
			name := string(worker)
			if published {
				name += "-published-before-sql"
			}
			t.Run(name, func(t *testing.T) {
				p := firstWorkdayDetailFixture(t)
				ctx := context.Background()
				member := p.plan.document.Members[0]
				monitor := "monitor|" + member.Domain + "|" + member.BoardID
				scrape := "scrape|" + p.f.task.Domain + "|" + p.f.task.ID
				inflight, tokens := "inflight:"+string(worker), "inflight_tokens:"+string(worker)
				if err := p.f.client.redis.ZAdd(ctx, inflight,
					redis.Z{Score: float64(time.Now().Add(-time.Minute).Unix()), Member: monitor},
					redis.Z{Score: float64(time.Now().Add(-time.Minute).Unix()), Member: scrape}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.HSet(ctx, tokens, monitor, "retained-legacy-monitor", scrape, "retained-legacy-scrape").Err(); err != nil {
					t.Fatal(err)
				}
				if published {
					if err := p.f.client.redis.Set(ctx, ownershipProjectionKey, p.plan.projection, 0).Err(); err != nil {
						t.Fatal(err)
					}
				}
				before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
				if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("activation accepted legacy inflight", err)
				}
				result, err := applyFirstFixture(t, p, true)
				if err != nil || result.State != "staged" {
					t.Fatal("inert cancellation refused legacy inflight", err)
				}
				delete(before, ownershipProjectionKey)
				if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
					t.Fatal("cancellation changed legacy queues, B0, canonical data or staged history")
				}
				if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("cancellation granted activation over legacy inflight", err)
				}
			})
		}
	}
}
