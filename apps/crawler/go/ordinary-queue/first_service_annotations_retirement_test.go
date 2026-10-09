package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstServiceAnnotationFixture(t *testing.T, variant string) firstOwnerFixture {
	var p firstOwnerFixture
	if variant == "api-rendered" {
		p = firstSharedDOMAPIFixture(t, "api-auto/rendered")
	} else {
		p = firstProviderBatchFixture(t, variant)
	}
	ctx := context.Background()
	var raw []byte
	if e := p.f.observer.QueryRow(ctx, "SELECT metadata FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&raw); e != nil {
		t.Fatal(e)
	}
	md := map[string]any{}
	if json.Unmarshal(raw, &md) != nil {
		t.Fatal("metadata")
	}
	md["defaults"] = map[string]any{"locations": []string{"retained annotation"}}
	md["rescrape_policy"] = "never"
	raw, _ = json.Marshal(md)
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(raw)); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(raw)).Err(); e != nil {
		t.Fatal(e)
	}
	plan, e := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if e != nil {
		t.Fatal("annotation stage", e)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealSharedServiceAnnotationsColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"dom", "sitemap", "api_sniffer", "api_sniffer/proxy", "api-rendered"}, firstServiceAnnotationFixture)
}
