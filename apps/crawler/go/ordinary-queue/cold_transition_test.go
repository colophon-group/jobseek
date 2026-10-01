package queue

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func coldCanonicalSnapshot(t *testing.T, f authorityFixture) string {
	t.Helper()
	var body string
	err := f.observer.QueryRow(context.Background(), `SELECT jsonb_build_object(
 'board',(SELECT to_jsonb(b) FROM job_board b WHERE id=$1::uuid),
 'posting',(SELECT to_jsonb(p) FROM job_posting p WHERE id=$1::uuid),
 'descriptions',(SELECT jsonb_agg(to_jsonb(d) ORDER BY locale) FROM descriptions d WHERE posting_id=$1::uuid),
 'receipts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY task_kind,task_id) FROM ordinary_worker_write_fence f WHERE board_id=$1::uuid))::text`, f.task.ID).Scan(&body)
	if err != nil {
		t.Fatal("canonical transition snapshot unavailable")
	}
	return body
}

func TestColdTransitionSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_transition.sql")
	if err != nil || string(body) != coldTransitionSchema {
		t.Fatal("journal schema differs from installed migration")
	}
}

func coldSpec(t *testing.T, f authorityFixture, before, prepared *OwnershipPlan) ColdTransitionSpec {
	t.Helper()
	s := ColdTransitionSpec{Version: coldTransitionVersion, TransitionID: ordinaryID(t), SourceRevision: prepared.SourceRevision(), PreviousEpoch: f.epoch,
		PreparedPlanSHA256: prepared.digest, PreviousB0ReceiptSHA256: strings.Repeat("1", 64), TargetB0ManifestSHA256: strings.Repeat("2", 64),
		ActiveReleaseSHA256: strings.Repeat("3", 64), TargetReleaseSHA256: strings.Repeat("4", 64), RollbackReleaseSHA256: strings.Repeat("3", 64), ColdAttestationSHA256: strings.Repeat("5", 64)}
	if before != nil {
		s.PreviousOrdinaryPlanSHA256 = before.digest
	}
	// Only this isolated *_ordinary_worker_test database may discard fixture
	// journals. The production trigger deliberately retains every intent.
	t.Cleanup(func() {
		if _, err := f.observer.Exec(context.Background(), "TRUNCATE public.crawler_ownership_reversal,public.crawler_ownership_transition"); err != nil {
			t.Error("private journal cleanup failed")
		}
		if _, err := f.observer.Exec(context.Background(), "TRUNCATE public.crawler_ownership_b0_target"); err != nil {
			t.Error("private target cleanup failed")
		}
	})
	return s
}

func TestColdTransitionRejectsAmbiguousOrUnboundIntent(t *testing.T) {
	s := ColdTransitionSpec{Version: coldTransitionVersion, TransitionID: "00000000-0000-4000-8000-000000000001", SourceRevision: strings.Repeat("a", 40), PreviousEpoch: 7,
		PreparedPlanSHA256: strings.Repeat("1", 64), TargetB0ManifestSHA256: strings.Repeat("2", 64), ActiveReleaseSHA256: strings.Repeat("3", 64), TargetReleaseSHA256: strings.Repeat("4", 64), RollbackReleaseSHA256: strings.Repeat("3", 64), ColdAttestationSHA256: strings.Repeat("5", 64)}
	body, _ := json.Marshal(s)
	hash := sha256.Sum256(body)
	if got, err := decodeColdTransition(string(body), hex.EncodeToString(hash[:])); err != nil || got != s {
		t.Fatal("canonical intent rejected")
	}
	for _, mutate := range []func(*ColdTransitionSpec){
		func(s *ColdTransitionSpec) { s.Version = "unknown" }, func(s *ColdTransitionSpec) { s.TransitionID = "unknown" },
		func(s *ColdTransitionSpec) { s.SourceRevision = "bad" }, func(s *ColdTransitionSpec) { s.PreviousEpoch = 0 },
		func(s *ColdTransitionSpec) { s.PreviousEpoch = 9999999999999 }, func(s *ColdTransitionSpec) { s.PreparedPlanSHA256 = "" },
		func(s *ColdTransitionSpec) { s.PreviousB0ReceiptSHA256 = "secret" }, func(s *ColdTransitionSpec) { s.TargetReleaseSHA256 = "" },
		func(s *ColdTransitionSpec) { s.ColdAttestationSHA256 = "" },
	} {
		bad := s
		mutate(&bad)
		encoded, _ := json.Marshal(bad)
		h := sha256.Sum256(encoded)
		if _, err := decodeColdTransition(string(encoded), hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("invalid intent admitted")
		}
	}
	for _, bad := range []string{string(body) + " ", strings.Replace(string(body), `"previous_epoch":7`, `"previous_epoch":8,"previous_epoch":7`, 1), strings.Replace(string(body), `"version":`, `"unknown":"secret","version":`, 1)} {
		h := sha256.Sum256([]byte(bad))
		if _, err := decodeColdTransition(bad, hex.EncodeToString(h[:])); !errors.Is(err, ErrAuthorityLost) || strings.Contains(err.Error(), "secret") {
			t.Fatal("ambiguous intent accepted or leaked")
		}
	}
}

func TestRealColdTransitionReservationRetiresOldOwnerWithoutActivation(t *testing.T) {
	for _, previousOwner := range []bool{false, true} {
		t.Run(map[bool]string{false: "first", true: "replacement"}[previousOwner], func(t *testing.T) {
			f := greenhouseAuthorityFixture(t)
			ctx := context.Background()
			var old *OwnershipPlan
			if previousOwner {
				old = stageFixturePlan(t, f, strings.Repeat("b", 40))
				activateFixturePlan(t, f, old)
			}
			prepared := stageFixturePlan(t, f, strings.Repeat("a", 40))
			s := coldSpec(t, f, old, prepared)
			before := snapshot(t, f.client)
			canonical := coldCanonicalSnapshot(t, f)
			digest, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s)
			if err != nil {
				t.Fatalf("begin: %v", err)
			}
			repeat, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s)
			if err != nil || repeat != digest {
				t.Fatal("intent retry changed identity")
			}
			var epoch int64
			_ = f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch)
			if epoch != f.epoch {
				t.Fatal("begin advanced sequence")
			}
			competing := s
			competing.TransitionID = ordinaryID(t)
			if _, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, competing); err == nil {
				t.Fatal("competing pending intent accepted")
			}
			p, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision)
			if err != nil || p == nil || p.Epoch() != f.epoch+1 || p.digest == prepared.digest {
				t.Fatalf("reserve: %v", err)
			}
			retry, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision)
			if err != nil || retry.digest != p.digest {
				t.Fatal("reservation retry allocated/adopted another identity")
			}
			var active int
			var phase string
			var recorded int64
			if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&active); err != nil || active != 0 {
				t.Fatal("reservation activated an owner")
			}
			if err := f.observer.QueryRow(ctx, "SELECT phase,routing_epoch FROM crawler_ownership_transition WHERE intent_sha256=$1", digest).Scan(&phase, &recorded); err != nil || phase != "reserved" || recorded != p.Epoch() {
				t.Fatal("reservation journal incomplete")
			}
			if old != nil {
				var state string
				if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", old.digest).Scan(&state); err != nil || state != "retired" {
					t.Fatal("old owner not retired")
				}
			}
			if !reflect.DeepEqual(before, snapshot(t, f.client)) {
				t.Fatal("database reservation mutated Redis")
			}
			assertCanonical(t, f, false)
			if coldCanonicalSnapshot(t, f) != canonical {
				t.Fatal("reservation changed canonical rows/deadlines/receipts")
			}
			if _, err := f.authority.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("old epoch retained claim authority")
			}
			for _, sql := range []string{"DELETE FROM crawler_ownership_transition WHERE intent_sha256=$1", "UPDATE crawler_ownership_transition SET payload=payload||' ' WHERE intent_sha256=$1", "UPDATE crawler_ownership_transition SET phase='pending',routing_epoch=NULL,reserved_plan_sha256=NULL WHERE intent_sha256=$1"} {
				if _, err := f.observer.Exec(ctx, sql, digest); err == nil {
					t.Fatal("retained intent mutated")
				}
			}
			changed := s
			changed.TransitionID = ordinaryID(t)
			if _, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, changed); err == nil {
				t.Fatal("competing transition accepted")
			}
		})
	}
}

func TestRealColdTransitionRecoversBurnedEpochAndUncertainCommit(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	claim := mustClaim(t, f)
	if _, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error { return effect(ctx, tx, claim, futureDue()) }); err != nil {
		t.Fatal("prior committed receipt unavailable")
	}
	canonical := coldCanonicalSnapshot(t, f)
	beforeRedis := snapshot(t, f.client)
	old := stageFixturePlan(t, f, strings.Repeat("b", 40))
	activateFixturePlan(t, f, old)
	prepared := stageFixturePlan(t, f, strings.Repeat("a", 40))
	s := coldSpec(t, f, old, prepared)
	digest, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s)
	if err != nil {
		t.Fatal(err)
	}
	// Private constraint forces failure AFTER nextval and old retirement, before
	// the replacement insert. No production fault hook or weakened fence exists.
	if _, err := f.observer.Exec(ctx, "ALTER TABLE ordinary_worker_ownership_plan ADD CONSTRAINT private_epoch_crash CHECK (routing_epoch <= "+strconv.FormatInt(f.epoch, 10)+")"); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "ALTER TABLE ordinary_worker_ownership_plan DROP CONSTRAINT IF EXISTS private_epoch_crash")
	})
	if p, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision); p != nil || err == nil {
		t.Fatal("faulted reservation escaped")
	}
	var burned int64
	var phase, state string
	_ = f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&burned)
	_ = f.observer.QueryRow(ctx, "SELECT phase FROM crawler_ownership_transition WHERE intent_sha256=$1", digest).Scan(&phase)
	_ = f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", old.digest).Scan(&state)
	if burned != f.epoch+1 || phase != "pending" || state != "active" {
		t.Fatal("crash did not preserve intent/atomic retirement while burning sequence")
	}
	if _, err := f.observer.Exec(ctx, "ALTER TABLE ordinary_worker_ownership_plan DROP CONSTRAINT private_epoch_crash"); err != nil {
		t.Fatal(err)
	}
	// A new coordinator process can use the same journal without opening an
	// Authority at an obsolete epoch or guessing the latest allocated value.
	pool, err := pgxpool.New(ctx, f.dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	p, err := ReserveColdOwnershipEpoch(ctx, pool, f.client, digest, s.SourceRevision)
	if err != nil || p == nil || p.Epoch() != burned+1 {
		t.Fatalf("burned epoch was adopted or recovery failed: %v", err)
	}
	for i := 0; i < 2; i++ {
		retry, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision)
		if err != nil || retry.digest != p.digest {
			t.Fatal("uncertain commit retry changed committed reservation")
		}
	}
	assertCanonical(t, f, true)
	if coldCanonicalSnapshot(t, f) != canonical || !reflect.DeepEqual(beforeRedis, snapshot(t, f.client)) {
		t.Fatal("crash/recovery changed committed rows/deadlines/receipts or queue projections")
	}
}

func TestRealColdTransitionRejectsDriftBeforeSequenceMutation(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	s := coldSpec(t, f, nil, p)
	digest, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, strings.Repeat("b", 40)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong compiled source accepted")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision); err == nil {
		t.Fatal("disabled cohort accepted")
	}
	var epoch int64
	_ = f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch)
	if epoch != f.epoch {
		t.Fatal("invalid candidate burned epoch")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=true WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	reserved, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := ReserveColdOwnershipEpoch(ctx, f.observer, f.client, digest, s.SourceRevision); err == nil {
		t.Fatal("reservation retry hid cohort drift")
	}
	_ = f.observer.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch)
	if epoch != reserved.Epoch() {
		t.Fatal("readback drift burned another epoch")
	}
}

func TestRealColdTransitionWaitsForExistingOrdinaryWrite(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	s := coldSpec(t, f, nil, p)
	blocker, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	var pid int
	if err := blocker.QueryRow(ctx, "SELECT pg_backend_pid()").Scan(&pid); err != nil {
		t.Fatal(err)
	}
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_xact_lock_shared($1)", OrdinaryLeaseBarrier); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { _, err := BeginColdOwnershipTransition(ctx, f.observer, f.client, s); done <- err }()
	for {
		select {
		case err := <-done:
			t.Fatalf("journal crossed existing writer: %v", err)
		default:
		}
		var waiting bool
		if err := f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1::integer=ANY(pg_blocking_pids(pid)))", pid).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("journal lock not observed")
		case <-time.After(10 * time.Millisecond):
		}
	}
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err := <-done; err != nil {
		t.Fatal(err)
	}
}
