package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestColdReversalSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_reversal.sql")
	if err != nil || string(body) != coldReversalSchema {
		t.Fatal("retirement journal schema differs from applied migration")
	}
}

func reversalSpec(t *testing.T, p publicationFixture, phase string) ColdReversalSpec {
	t.Helper()
	return ColdReversalSpec{Version: coldReversalVersion, ReversalID: ordinaryID(t), ForwardIntentSHA256: p.intent,
		SourceRevision: p.spec.SourceRevision, SourceEpoch: p.plan.Epoch(), SourcePlanSHA256: p.plan.digest, SourcePhase: phase,
		RollbackReleaseSHA256: p.spec.RollbackReleaseSHA256, RollbackOrdinaryPlanSHA256: p.spec.PreviousOrdinaryPlanSHA256,
		RollbackB0ReceiptSHA256: p.spec.PreviousB0ReceiptSHA256, ColdAttestationSHA256: strings.Repeat("6", 64)}
}

func TestColdReversalRejectsAmbiguousIdentity(t *testing.T) {
	s := ColdReversalSpec{Version: coldReversalVersion, ReversalID: "00000000-0000-4000-8000-000000000001", ForwardIntentSHA256: strings.Repeat("1", 64), SourceRevision: strings.Repeat("a", 40), SourceEpoch: 146,
		SourcePlanSHA256: strings.Repeat("2", 64), SourcePhase: "active", RollbackReleaseSHA256: strings.Repeat("3", 64), ColdAttestationSHA256: strings.Repeat("4", 64)}
	body, _ := json.Marshal(s)
	hash := sha256.Sum256(body)
	if got, err := DecodeColdReversalSpec(string(body), hex.EncodeToString(hash[:])); err != nil || got != s {
		t.Fatal("canonical protected reversal rejected")
	}
	for _, bad := range []string{string(body) + " ", strings.TrimSuffix(string(body), "}") + `,"source_epoch":146}`, strings.TrimSuffix(string(body), "}") + `,"unknown":"secret"}`, strings.Replace(string(body), `"source_phase":"active"`, `"source_phase":"reversed"`, 1), strings.Replace(string(body), `"source_epoch":146`, `"source_epoch":9999999999999`, 1)} {
		h := sha256.Sum256([]byte(bad))
		if _, err := DecodeColdReversalSpec(bad, hex.EncodeToString(h[:])); !errors.Is(err, ErrConfiguration) || strings.Contains(err.Error(), "secret") {
			t.Fatal("ambiguous reversal accepted or exposed input")
		}
	}
}

func TestRealColdReversalIntentAndFreshRetirementAcrossForwardPhases(t *testing.T) {
	for _, phase := range []string{"reserved", "publishing", "published", "active"} {
		t.Run(phase, func(t *testing.T) {
			p := realPublication(t)
			ctx := context.Background()
			if phase == "publishing" {
				if err := PrepareColdOwnershipPublication(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			}
			if phase == "published" || phase == "active" {
				if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			}
			if phase == "active" {
				if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
					t.Fatal(err)
				}
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			s := reversalSpec(t, p, phase)
			digest, err := BeginColdOwnershipReversal(ctx, p.f.observer, s)
			if err != nil {
				t.Fatal(err)
			}
			if retry, err := BeginColdOwnershipReversal(ctx, p.f.observer, s); err != nil || retry != digest {
				t.Fatal("begin retry changed retained identity")
			}
			pending, err := InspectColdReversal(ctx, p.f.observer, digest, s.SourceRevision)
			if err != nil || pending.phase != "pending" || pending.retirement != 0 || pending.spec != s {
				t.Fatal("pending retirement inspection adopted an epoch")
			}
			if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err == nil {
				t.Fatal("forward activation escaped retained reversal")
			}
			retired, err := ReserveColdReversalEpoch(ctx, p.f.observer, digest, s.SourceRevision)
			if err != nil || retired.phase != "reserved" || retired.retirement != p.plan.Epoch()+1 {
				t.Fatal("fresh retirement failed")
			}
			if repeat, err := ReserveColdReversalEpoch(ctx, p.f.observer, digest, s.SourceRevision); err != nil || !reflect.DeepEqual(repeat, retired) {
				t.Fatal("retirement retry allocated or adopted another epoch")
			}
			if _, err := p.f.observer.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1", p.intent); err == nil {
				t.Fatal("retirement alone released claims before verified restoration")
			}
			unselected, err := OpenAuthority(ctx, p.f.dsn, p.f.client, retired.retirement)
			if err != nil {
				t.Fatal(err)
			}
			defer unselected.Close()
			if _, err := unselected.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unselected claims escaped unfinished restoration")
			}
			for _, sql := range []string{"DELETE FROM crawler_ownership_reversal WHERE reversal_sha256=$1", "UPDATE crawler_ownership_reversal SET payload=payload||' ' WHERE reversal_sha256=$1", "UPDATE crawler_ownership_reversal SET retirement_epoch=retirement_epoch+1 WHERE reversal_sha256=$1"} {
				if _, err := p.f.observer.Exec(ctx, sql, digest); err == nil {
					t.Fatal("retained reversal identity/epoch changed")
				}
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("retirement altered Redis, canonical rows or due/receipt state")
			}
		})
	}
}

func TestRealColdReversalAllowsDisabledChangedAndLostCandidateWithCompletedReceipt(t *testing.T) {
	for _, fault := range []string{"disabled", "changed", "missing_witness", "redis_loss"} {
		t.Run(fault, func(t *testing.T) {
			p := realPublication(t)
			a := activateJointFixture(t, p)
			ctx := context.Background()
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("joint receipt fixture claim unavailable")
			}
			receipt, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
				_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid", claim.boardID)
				return err
			})
			if err != nil || receipt == nil {
				t.Fatal("real completed future-due receipt unavailable")
			}
			switch fault {
			case "disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid OR id=$2::uuid", claim.boardID, p.target.document.Boards[0].ID)
			case "changed":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET check_interval_minutes=61 WHERE id=$1::uuid OR id=$2::uuid", claim.boardID, p.target.document.Boards[0].ID)
			case "missing_witness":
				err = p.f.client.redis.Del(ctx, coldPublicationKey).Err()
			case "redis_loss":
				err = p.f.client.redis.FlushDB(ctx).Err() // Validated wholly owned fixture.
			}
			if err != nil {
				t.Fatal("owned candidate fault failed")
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			s := reversalSpec(t, p, "active")
			digest, err := BeginColdOwnershipReversal(ctx, p.f.observer, s)
			if err != nil {
				t.Fatal("disabled/lost source could not enter explicit cold reversal")
			}
			called := false
			if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
				t.Fatal("reversing source retained callback authority")
			}
			if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("reversing source retained heartbeat authority")
			}
			if err := a.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("reversing source erased completed receipt/lease")
			}
			if _, err := ReserveColdReversalEpoch(ctx, p.f.observer, digest, s.SourceRevision); err != nil {
				t.Fatal("disabled/lost source could not reserve fresh retirement")
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("retirement changed canonical receipt/future due or Redis after source drift")
			}
		})
	}
}

func TestRealColdReversalRecoversBurnedEpochWithoutAdoption(t *testing.T) {
	p := realPublication(t)
	_ = activateJointFixture(t, p)
	ctx := context.Background()
	s := reversalSpec(t, p, "active")
	digest, err := BeginColdOwnershipReversal(ctx, p.f.observer, s)
	if err != nil {
		t.Fatal(err)
	}
	before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_reversal ADD CONSTRAINT private_retirement_crash CHECK (retirement_epoch IS NULL)"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "ALTER TABLE crawler_ownership_reversal DROP CONSTRAINT IF EXISTS private_retirement_crash")
	})
	if result, err := ReserveColdReversalEpoch(ctx, p.f.observer, digest, s.SourceRevision); result != nil || err == nil {
		t.Fatal("faulted reservation escaped")
	}
	var burned int64
	var ownerState string
	_ = p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&burned)
	_ = p.f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", p.plan.digest).Scan(&ownerState)
	pending, err := InspectColdReversal(ctx, p.f.observer, digest, s.SourceRevision)
	if err != nil || pending.retirement != 0 || pending.phase != "pending" || burned != s.SourceEpoch+1 || ownerState != "active" {
		t.Fatal("failed retirement lost pending intent or adopted burned epoch")
	}
	if _, err := p.f.observer.Exec(ctx, "ALTER TABLE crawler_ownership_reversal DROP CONSTRAINT private_retirement_crash"); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, p.f.dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	retired, err := ReserveColdReversalEpoch(ctx, pool, digest, s.SourceRevision)
	if err != nil || retired.retirement != burned+1 {
		t.Fatal("independent coordinator adopted burned epoch")
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("retirement recovery replayed canonical or Redis effects")
	}
	// A later high-water is not the retained reservation. Inspection preserves
	// history, while authority refuses to adopt it or allocate again.
	if _, err := p.f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	if _, err := ReserveColdReversalEpoch(ctx, pool, digest, s.SourceRevision); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("recorded reservation adopted a later high-water")
	}
	if retained, err := InspectColdReversal(ctx, pool, digest, s.SourceRevision); err != nil || retained.retirement != retired.retirement {
		t.Fatal("history inspection adopted current high-water")
	}
}

func TestRealColdReversalRejectsWrongReleaseAndWaitsForLeaseBarrier(t *testing.T) {
	p := realPublication(t)
	_ = activateJointFixture(t, p)
	ctx := context.Background()
	s := reversalSpec(t, p, "active")
	for _, mutate := range []func(*ColdReversalSpec){
		func(s *ColdReversalSpec) { s.RollbackReleaseSHA256 = strings.Repeat("9", 64) },
		func(s *ColdReversalSpec) { s.RollbackOrdinaryPlanSHA256 = strings.Repeat("9", 64) },
		func(s *ColdReversalSpec) { s.RollbackB0ReceiptSHA256 = strings.Repeat("9", 64) },
		func(s *ColdReversalSpec) { s.SourceEpoch++ },
		func(s *ColdReversalSpec) { s.SourcePlanSHA256 = strings.Repeat("9", 64) },
		func(s *ColdReversalSpec) { s.SourcePhase = "reserved" },
		func(s *ColdReversalSpec) { s.SourceRevision = strings.Repeat("9", 40) },
	} {
		bad := s
		mutate(&bad)
		if _, err := BeginColdOwnershipReversal(ctx, p.f.observer, bad); err == nil {
			t.Fatal("wrong retained release/owner identity began reversal")
		}
	}
	var current int64
	_ = p.f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&current)
	if current != s.SourceEpoch {
		t.Fatal("rejected reversal burned an epoch")
	}
	blocker, err := p.f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := BeginColdOwnershipReversal(ctx, p.f.observer, s); done <- err }()
	deadline := time.Now().Add(3 * time.Second)
	for {
		var waiting bool
		_ = p.f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted)").Scan(&waiting)
		if waiting {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("real reversal did not wait behind active lease barrier")
		}
		time.Sleep(10 * time.Millisecond)
	}
	select {
	case <-done:
		t.Fatal("reversal passed an active write lease")
	default:
	}
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
