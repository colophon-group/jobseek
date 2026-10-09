package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstFeedCollisionFixture(t *testing.T, provider string) firstOwnerFixture {
	p := firstProviderBatchFixture(t, provider)
	ctx := context.Background()
	var md map[string]any
	if json.Unmarshal([]byte(p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "metadata").Val()), &md) != nil {
		t.Fatal("fixture metadata")
	}
	md["url_transform"] = map[string]any{"find": `^https://example\.com/(?:en|de)/jobs/([^/]+)$`, "replace": `https://example.com/jobs/\1`, "collision_policy": "prefer_source_pattern", "collision_preferred_source_patterns": []string{"/en/", "/de/"}, "collision_canonical_identity_regex": `^https://example\.com/jobs/([^/]+)$`, "collision_source_identity_regex": `^https://example\.com/(?:en|de)/jobs/([^/]+)$`, "collision_stream_buffer_limit": 500}
	raw, _ := json.Marshal(md)
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(raw)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(raw)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("collision stage", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}
func TestRealFeedCollisionColdRetirementAndConservation(t *testing.T) {
	testProviderColdRetirement(t, []string{"rss", "api_sniffer", "dom"}, firstFeedCollisionFixture)
}
