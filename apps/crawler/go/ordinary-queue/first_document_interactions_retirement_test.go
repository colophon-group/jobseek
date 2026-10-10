package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstDocumentInteractionFixture(t *testing.T, kind string) firstOwnerFixture {
	p := firstRenderedMonitorFixture(t)
	action := map[string]any{"action": kind, "selector": "button"}
	switch kind {
	case "wait_for":
		action["state"] = "attached"
	case "paginate_collect":
		delete(action, "selector")
		action["next_selector"] = "a.next"
	}
	pipeline := []any{action}
	if kind == "long-pipeline" {
		pipeline = []any{}
		for i := 0; i < 36; i++ {
			pipeline = append(pipeline, map[string]any{"action": "wait", "ms": 0})
		}
	}
	md, _ := json.Marshal(map[string]any{"render": true, "actions": pipeline, "scraper_type": "json-ld"})
	ctx := context.Background()
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(md)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(md)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("interaction ownership staging", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealDocumentInteractionsColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"click", "wait_for", "repeat", "paginate_collect", "long-pipeline"}, firstDocumentInteractionFixture)
}
