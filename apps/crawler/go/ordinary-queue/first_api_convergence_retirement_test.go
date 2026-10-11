package queue

import (
	"context"
	"strings"
	"testing"
)

func firstAPIConvergenceFixture(t *testing.T, _ string) firstOwnerFixture {
	p := firstProviderBatchFixture(t, "api_sniffer")
	f, ctx := p.f, context.Background()
	md := `{"api_url":"https://example.com/api?page=1","json_path":"jobs","total_path":"total","url_field":"url","pagination":{"style":"page","param_name":"page","max_pages":3},"pagination_convergence":{"max_passes":3,"required_no_growth_passes":2,"identity_by":["url"]},"scraper_type":"skip"}`
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", f.task.ID, md); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", md).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, nil)
	if err != nil {
		t.Fatal("convergence cold fixture stage", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealAPIConvergenceColdRetirementAndConservation(t *testing.T) {
	testProviderColdRetirement(t, []string{"api_sniffer/convergence"}, firstAPIConvergenceFixture)
}
