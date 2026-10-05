package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

func TestRealStagedOverlappingMonitorCancellationPreservesB0(t *testing.T) {
	for _, mode := range []string{"clean", "changed-b0", "foreign-projection"} {
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
			if mode == "clean" {
				if e != nil || result.State != "staged" {
					t.Fatal("inert overlap could not cancel", e)
				}
			} else if e == nil {
				t.Fatal("changed authority accepted")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
				t.Fatal("cancellation changed B0, canonical data or staged history")
			}
			if mode == "clean" {
				if _, e := applyFirstFixture(t, p, false); e == nil {
					t.Fatal("cancelled overlap gained activation authority")
				}
			}
		})
	}
}
