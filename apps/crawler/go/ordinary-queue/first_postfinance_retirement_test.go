package queue

import (
	"context"
	"strings"
	"testing"
)

func firstPostfinanceFixture(t *testing.T, _ string) firstOwnerFixture {
	p := firstProviderBatchFixture(t, "rss")
	f, ctx := p.f, context.Background()
	md := `{"preset":"successfactors","feed_url":"https://jobs.postfinance.ch/googlefeed.xml","scraper_type":"skip","identity_migration":"postfinance-swiss-post-stable-id-v1","_monitor_config_fingerprint":"` + postfinanceMigrationFingerprint + `","recent_discovered_counts":[1,1,1]}`
	if _, err := f.observer.Exec(ctx, "UPDATE company SET slug='postfinance' WHERE id=$1::uuid", f.company); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_slug='postfinance-careers',board_url=$2,metadata=$3::jsonb WHERE id=$1::uuid", f.task.ID, postfinanceMigrationBoardURL, md); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url='https://career.post.ch/de',source_identity='https://career.post.ch/de' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_slug", "postfinance-careers", "board_url", postfinanceMigrationBoardURL, "metadata", md).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, nil)
	if err != nil {
		t.Fatal("PostFinance cold fixture stage", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealPostfinanceMigrationColdRetirementAndConservation(t *testing.T) {
	testProviderColdRetirement(t, []string{"rss/postfinance"}, firstPostfinanceFixture)
}
