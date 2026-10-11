package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstLiveIdentityFixture(t *testing.T, variant string) firstOwnerFixture {
	var p firstOwnerFixture
	if variant == "api-live" {
		p = firstSharedDOMAPIFixture(t, "api-auto/rendered")
	} else {
		p = firstProviderBatchFixture(t, "rss")
	}
	ctx := context.Background()
	var md map[string]any
	if json.Unmarshal([]byte(p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "metadata").Val()), &md) != nil {
		t.Fatal("metadata")
	}
	if variant == "api-live" {
		md["api_url_match"] = "example.com/*"
	} else {
		if json.Unmarshal([]byte(sfRSSIdentityMetadata), &md) != nil {
			t.Fatal("identity collision metadata")
		}
		md["preset"] = "successfactors"
		md["feed_url"] = "https://example.com/googlefeed.xml"
		md["resolve_job_invite_identity"] = true
	}
	raw, _ := json.Marshal(md)
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(raw)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(raw)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("identity stage", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealLiveAPIAndRSSIdentityColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api-live", "rss-identity"}, firstLiveIdentityFixture)
}
