package queue

import (
	"context"
	"testing"
)

func firstNativeBrowserProviderFixture(t *testing.T, provider string) firstOwnerFixture {
	p := firstRenderedMonitorFixture(t)
	board := "https://airtel.darwinbox.in/ms/candidate/careers"
	if provider == "bytedance" {
		board = "https://jobs.bytedance.com/experienced/position"
	}
	metadata := `{"scraper_type":"skip"}`
	if provider == "brassring" {
		board = "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
	} else if provider == "accenture" {
		board = "https://www.accenture.com/us-en/careers/jobsearch"
		metadata = `{"country":"USA","language":"en","site":"us-en","scraper_type":"skip"}`
	} else if provider == "accenture/captured" {
		provider, board = "accenture", "https://www.accenture.com/fr-fr/careers/jobsearch"
		metadata = `{"country":"France","language":"fr","site":"fr-fr","endpoint":"jobsearch/result","scraper_type":"skip"}`
	}
	ctx := context.Background()
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type=$2,board_url=$3,metadata=$4::jsonb WHERE id=$1::uuid", p.f.task.ID, provider, board, metadata); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "crawler_type", provider, "board_url", board, "metadata", metadata).Err(); e != nil {
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
func TestRealNativeBrowserProviderColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"darwinbox", "bytedance", "brassring", "accenture", "accenture/captured"}, firstNativeBrowserProviderFixture)
}
