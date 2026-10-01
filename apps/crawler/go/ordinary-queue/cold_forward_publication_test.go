package queue

import (
	"context"
	"errors"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

func forwardPublicationFixture(t *testing.T, complete bool) (publicationFixture, *ColdB0ForwardPlan, *coldForwardPublicationApproval) {
	t.Helper()
	p, plan, control := retainedForwardApplication(t, false)
	approval := &coldForwardPublicationApproval{ColdB0ForwardCompletionBinding{plan.digest, strings.Repeat("a", 64)}, control}
	if complete {
		state, err := applyRetainedColdB0Forward(context.Background(), p.f.observer, p.f.client, control, plan.digest, p.spec.SourceRevision, p.target)
		if err != nil {
			t.Fatal(err)
		}
		approval.binding.ReceiptSHA256 = state.digest
	}
	return p, plan, approval
}

func TestRealColdForwardPublicationRequiresCommittedTransferBeforeEffects(t *testing.T) {
	for _, fault := range []string{"prepared", "receipt", "plan", "canonical", "source", "producer"} {
		t.Run(fault, func(t *testing.T) {
			p, plan, approval := forwardPublicationFixture(t, fault != "prepared")
			ctx := context.Background()
			switch fault {
			case "receipt":
				approval.binding.ReceiptSHA256 = strings.Repeat("f", 64)
			case "plan":
				approval.binding.PlanSHA256 = strings.Repeat("f", 64)
			case "canonical":
				_, _ = p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 second' WHERE id=$1::uuid", plan.document.PostgresRows[0].ID)
			case "source":
				_ = p.f.client.redis.HSet(ctx, "scrape:"+plan.document.Tasks[0].PostingID, "description_r2_hash", "8").Err()
			case "producer":
				_ = p.f.client.redis.HSet(ctx, "lightpanda-b0:producer-owner", "board_slug:foreign-careers", "1").Err()
			}
			before, canonical := snapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
			if err := prepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("uncompleted/changed transfer admitted publication", fault, err)
			}
			if publicationPhase(t, p) != "reserved" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldB0CanonicalSnapshot(t, p) {
				t.Fatal("failed transfer attestation changed authority/data")
			}
		})
	}
}

func TestRealColdForwardPublicationCannotBypassRetainedApproval(t *testing.T) {
	for _, complete := range []bool{false, true} {
		t.Run(strconv.FormatBool(complete), func(t *testing.T) {
			p, _, _ := forwardPublicationFixture(t, complete)
			ctx := context.Background()
			before := snapshot(t, p.f.client)
			if err := PrepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("older command bypassed retained transfer approval", err)
			}
			if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("older publication bypassed retained transfer approval", err)
			}
			if publicationPhase(t, p) != "reserved" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("refused bypass changed routing")
			}
		})
	}
}

func TestRealColdForwardPublicationMissingApprovalCannotCreateAuthority(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	approval := &coldForwardPublicationApproval{ColdB0ForwardCompletionBinding{strings.Repeat("e", 64), strings.Repeat("f", 64)}, &forwardFixtureControl{p: p}}
	before := snapshot(t, p.f.client)
	if err := prepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("missing transfer history admitted publication", err)
	}
	if publicationPhase(t, p) != "reserved" || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
		t.Fatal("missing history was reconstructed")
	}
}

func TestRealColdForwardPublicationCompletionSurvivesWitnessAndSQLSeams(t *testing.T) {
	p, _, approval := forwardPublicationFixture(t, true)
	ctx := context.Background()
	before, canonical := forwardRedisSnapshot(t, p.f.client), coldB0CanonicalSnapshot(t, p)
	// Fail SQL after the pending witness is written. Exact reserved-phase retry
	// must observe this known witness rather than recreate transfer approval.
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition ADD CONSTRAINT private_forward_pending CHECK (phase <> 'publishing')"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT IF EXISTS private_forward_pending")
	})
	if err := prepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err == nil || publicationPhase(t, p) != "reserved" {
		t.Fatal("pending SQL failure lost reserved containment")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT private_forward_pending"); err != nil {
		t.Fatal(err)
	}
	if err := prepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err != nil {
		t.Fatal("exact pending witness retry failed", err)
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition ADD CONSTRAINT private_forward_published CHECK (phase <> 'published')"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT IF EXISTS private_forward_published")
	})
	if _, err := publishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err == nil || publicationPhase(t, p) != "publishing" {
		t.Fatal("post-SAVE SQL failure lost publishing containment")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_transition DROP CONSTRAINT private_forward_published"); err != nil {
		t.Fatal(err)
	}
	restartPublicationRedisWithoutSave(t, p.f.client)
	if _, err := publishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err != nil {
		t.Fatal("exact persisted publication retry failed", err)
	}
	if _, err := activateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err != nil {
		t.Fatal("completed transfer could not activate", err)
	}
	after := forwardRedisSnapshot(t, p.f.client)
	delete(after, ownershipProjectionKey)
	delete(after, coldPublicationKey)
	if !reflect.DeepEqual(before, after) {
		for key, old := range before {
			if after[key] != old {
				t.Errorf("changed logical Redis key %q", key)
			}
		}
		for key := range after {
			if _, exists := before[key]; !exists {
				t.Errorf("new logical Redis key %q", key)
			}
		}
		t.Fatal("publication changed logical queue evidence")
	}
	if canonical != coldB0CanonicalSnapshot(t, p) {
		t.Fatal("publication changed canonical data")
	}
	// Legitimate canonical schedule progression after activation is not replay
	// authority and must not invalidate immutable historical transfer identity.
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=next_scrape_at+interval '1 hour' WHERE board_id=$1::uuid", p.target.document.Boards[0].ID); err != nil {
		t.Fatal(err)
	}
	if _, err := activateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target, approval); err != nil {
		t.Fatal("active history was incorrectly treated as a cold snapshot", err)
	}
}
