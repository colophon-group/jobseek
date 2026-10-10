package queue

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func firstSuccessFactorsRMKFixture(t *testing.T, variant string) firstOwnerFixture {
	p := firstProviderBatchFixture(t, "rss")
	scraper := "skip"
	if strings.HasPrefix(variant, "items") {
		scraper = "json-ld"
	}
	metadata := map[string]any{"preset": "successfactors", "variant": "rmk", "brand": "Fixture", "locale": "en_US", "scraper_type": scraper, "proxy": strings.HasSuffix(variant, "/proxy")}
	if scraper != "skip" {
		metadata["scraper_config"] = map[string]any{"enrich": []string{"description"}}
	}
	md, _ := json.Marshal(metadata)
	ctx := context.Background()
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,metadata=$3::jsonb WHERE id=$1::uuid", p.f.task.ID, "https://example.com/Fixture/jobs", string(md)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "board_url", "https://example.com/Fixture/jobs", "metadata", string(md)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("RMK ownership staging", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}
func TestRealSuccessFactorsRMKColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"skip", "skip/proxy", "items", "items/proxy"}, firstSuccessFactorsRMKFixture)
}
