package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstDOMResourceFixture(t *testing.T, kind string) firstOwnerFixture {
	p := firstRenderedMonitorFixture(t)
	md, _ := json.Marshal(map[string]any{"render": true, "resource_policy": kind, "bot_protection": false, "scraper_type": "json-ld"})
	ctx := context.Background()
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(md)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(md)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("resource option ownership staging", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealDOMAutoResourceColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"auto", "none"}, firstDOMResourceFixture)
}
