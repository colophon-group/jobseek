package queue

import (
	"context"
	"errors"
	"testing"
)

func firstRenderedDetailFixture(t *testing.T) firstOwnerFixture {
	return firstIndependentDetailFixture(t, `{"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}]}}`, "https://jobs.example.net/job/native-rendered", "jobs.example.net", Browser)
}

func TestRealRenderedDetailColdRetirementRestoresBrowserQueue(t *testing.T) {
	testIndependentDetailColdRetirement(t, firstRenderedDetailFixture)
}

func TestRealRenderedDetailRetiredAttemptDoesNotBlockLaterColdReversal(t *testing.T) {
	old := firstRenderedDetailFixture(t)
	firstRetirementClaim(t, old)
	if _, err := applyFirstFixture(t, old, true); err != nil {
		t.Fatal(err)
	}
	current := firstOwnershipFixtureHistory(t, true)
	firstRetirementClaim(t, current)
	if _, err := applyFirstFixture(t, current, true); err != nil {
		t.Fatal("retained older Browser attempt blocked later reversal", err)
	}
	if firstFixtureState(t, old) != "retired" || firstFixtureState(t, current) != "retired" {
		t.Fatal("later reversal changed retained ownership history")
	}
}

func TestRealRenderedDetailCannotOverlapVerifiedB0Cohort(t *testing.T) {
	p := firstRenderedDetailFixture(t)
	ctx := context.Background()
	b0 := p.target.document.Boards[0].ID
	// Give the already canonical B0 board the exact supported browser parser,
	// then capture its new canonical witness before staging the overlap.
	metadata := `{"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}]}}`
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb,crawler_type='dom',throttle_key='jobs.example.test' WHERE id=$1::uuid", b0, metadata); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+b0, "metadata", metadata, "crawler_type", "dom", "throttle_key", "jobs.example.test").Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid WHERE id=$1::uuid", p.f.task.ID, b0); err != nil {
		t.Fatal(err)
	}
	target, err := CaptureColdB0Target(ctx, p.f.observer, p.f.client, p.f.epoch, p.target.document.Namespace, p.target.document.ShardID, p.target.document.Cohort, []byte(p.target.lua))
	if err != nil {
		t.Fatal(err)
	}
	p.target = target
	seedPublicationB0(t, p.f.client, target, p.f.epoch)
	p.plan, err = p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.plan.document.Members[0].BoardID}, []string{b0})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("ordinary browser adopted a B0-owned board", err)
	}
	if firstFixtureState(t, p) != "staged" || p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
		t.Fatal("overlap refusal published ownership")
	}
}
