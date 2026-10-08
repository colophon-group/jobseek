package queue

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestRealFirstUKGActivationAfterEquivalentLegacyDiscovery(t *testing.T) {
	for _, mode := range []string{"listing", "identifiers", "both", "other-target", "changed-parser", "foreign-cache"} {
		t.Run(mode, func(t *testing.T) {
			ctx := context.Background()
			p := firstProviderBatchFixture(t, "ukg")
			update := func(raw string, canonical bool) {
				t.Helper()
				if canonical {
					if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, raw); e != nil {
						t.Fatal(e)
					}
				}
				if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", raw).Err(); e != nil {
					t.Fatal(e)
				}
			}
			update(ukgBindingMetadata, true)
			plan, e := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, []string{p.f.task.ID})
			if e != nil {
				t.Fatal("stage monitor and independent detail", e)
			}
			p.plan = plan
			t.Cleanup(func() {
				_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
			})
			var md map[string]any
			json.Unmarshal([]byte(ukgBindingMetadata), &md)
			if mode == "listing" || mode == "both" || mode == "other-target" || mode == "foreign-cache" {
				md["listing_url"] = ukgBindingListing
			}
			if mode == "identifiers" || mode == "both" {
				md["host"], md["tenant"], md["board_id"] = "recruiting.ultipro.com", "ABC123", "11111111-1111-1111-1111-111111111111"
			}
			raw, _ := json.Marshal(md)
			if mode == "other-target" {
				raw = []byte(strings.Replace(string(raw), "ABC123", "DEF456", 1))
			}
			if mode == "changed-parser" {
				raw = []byte(strings.Replace(string(raw), `"description":"description"`, `"description":"other"`, 1))
			}
			if mode == "foreign-cache" {
				raw = []byte(strings.Replace(string(raw), "recruiting.ultipro.com", "foreign.example.com", 1))
			}
			update(string(raw), mode != "foreign-cache")
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			result, e := applyFirstFixture(t, p, false)
			equivalent := mode == "listing" || mode == "identifiers" || mode == "both"
			if !equivalent {
				if !errors.Is(e, ErrAuthorityLost) || firstFixtureState(t, p) != "staged" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
					t.Fatal("changed target/parser/cache granted authority or modified state", e)
				}
				return
			}
			if e != nil || result.State != "active" {
				t.Fatal("equivalent legacy discovery blocked activation", e)
			}
			loaded, e := p.f.authority.LoadActiveOwnership(ctx, plan.SHA256(), plan.SourceRevision())
			if e != nil || loaded.MemberCount() != 1 || loaded.DetailBoardCount() != 1 {
				t.Fatal("live monitor/detail ownership lost binding", e)
			}
			after := snapshot(t, p.f.client)
			delete(after, ownershipProjectionKey)
			if !reflect.DeepEqual(before, after) || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("activation changed schedules or canonical data")
			}
			result, e = applyFirstFixture(t, p, true)
			if e != nil || result.State != "retired" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("cold reversal failed or changed conserved state", e)
			}
		})
	}
}
