package queue

import (
	"context"
	"testing"
)

func firstRemainingRenderedFixture(t *testing.T, provider string) firstOwnerFixture {
	p := firstRenderedMonitorFixture(t)
	board, md := "https://www.iihf.com/en/static/5082/jobs", remainingIIHFMetadata
	if provider == "nextdata" {
		board, md = "https://www.revolut.com/careers/", remainingRevolutMetadata
	}
	ctx := context.Background()
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type=$2,board_url=$3,metadata=$4::jsonb WHERE id=$1::uuid", p.f.task.ID, provider, board, md); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "crawler_type", provider, "board_url", board, "metadata", md).Err(); e != nil {
		t.Fatal(e)
	}
	plan, e := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if e != nil {
		t.Fatal(e)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealRemainingRenderedAndOracleProxyColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"nextdata", "inline"}, firstRemainingRenderedFixture)
	testProviderColdRetirement(t, []string{"oracle_hcm/proxy"}, firstProviderBatchFixture)
	testFirstAPIDetailRetirement(t, "oracle_hcm/proxy")
}
