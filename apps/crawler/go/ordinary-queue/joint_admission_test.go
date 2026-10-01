package queue

import (
	"context"
	"errors"
	"os"
	"reflect"
	"strconv"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func TestJointTargetSchemaMatchesAppliedMigration(t *testing.T) {
	data, err := os.ReadFile("../../src/migrations/sql/crawler_ownership_b0_target.sql")
	if err != nil || string(data) != jointTargetSchema {
		t.Fatal("retained target schema differs from migration")
	}
	data, err = os.ReadFile("../../src/lua/ordinary_joint_admission.lua")
	if err != nil || string(data) != jointAdmissionLua {
		t.Fatal("legacy and native joint admission scripts differ")
	}
}

func activateJointFixture(t *testing.T, p publicationFixture) *Authority {
	t.Helper()
	ctx := context.Background()
	if _, err := PublishColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	if _, err := ActivateColdOwnership(ctx, p.f.observer, p.f.client, p.intent, p.spec.SourceRevision, p.target); err != nil {
		t.Fatal(err)
	}
	a, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch(), p.plan.digest, p.spec.SourceRevision, []byte(p.target.lua))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	return a
}

func TestRealJointAdmissionRequiresInstalledAuditAndRetainsTarget(t *testing.T) {
	p := realPublication(t)
	ctx := context.Background()
	before := snapshot(t, p.f.client)
	canonical := coldCanonicalSnapshot(t, p.f)
	unselected, err := OpenAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch())
	if err != nil {
		t.Fatal(err)
	}
	defer unselected.Close()
	if _, err := unselected.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unselected claimant ran during reserved joint transition")
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("blocked unselected claimant changed state")
	}
	a := activateJointFixture(t, p)
	if _, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch(), p.plan.digest, p.spec.SourceRevision); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("joint owner admitted without installed source-pinned audit")
	}
	if _, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch(), p.plan.digest, p.spec.SourceRevision, []byte("arbitrary caller script")); !errors.Is(err, ErrConfiguration) {
		t.Fatal("caller script granted joint authority")
	}
	if err := a.AttestOwnershipProjection(ctx, p.plan.ProjectionSHA1()); err != nil {
		t.Fatal(err)
	}
	var target string
	if err := p.f.observer.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_target WHERE target_sha256=$1", p.target.digest).Scan(&target); err != nil || target != p.target.body {
		t.Fatal("exact target not retained with publishing phase")
	}
	for _, sql := range []string{"UPDATE crawler_ownership_b0_target SET payload=payload||' ' WHERE target_sha256=$1", "DELETE FROM crawler_ownership_b0_target WHERE target_sha256=$1"} {
		if _, err := p.f.observer.Exec(ctx, sql, p.target.digest); err == nil {
			t.Fatal("retained target changed or was deleted")
		}
	}
}

func TestRealJointAdmissionFaultsRejectBeforePopAndConserveState(t *testing.T) {
	for _, mode := range []string{"missing_marker", "pending_marker", "marker_type", "marker_ttl", "projection_ttl", "route_epoch", "route_ttl", "producer_ttl", "producer_cohort", "selector", "record", "canonical_disabled", "redis_config", "target_missing", "journal_phase"} {
		t.Run(mode, func(t *testing.T) {
			p := realPublication(t)
			a := activateJointFixture(t, p)
			ctx := context.Background()
			r := p.f.client.redis
			var err error
			switch mode {
			case "missing_marker":
				err = r.Del(ctx, coldPublicationKey).Err()
			case "pending_marker":
				err = r.Set(ctx, coldPublicationKey, publicationMarker(p.intent, p.spec, p.plan, "pending"), 0).Err()
			case "marker_type":
				err = r.Del(ctx, coldPublicationKey).Err()
				if err == nil {
					err = r.HSet(ctx, coldPublicationKey, "private", "not-a-marker").Err()
				}
			case "marker_ttl":
				err = r.Expire(ctx, coldPublicationKey, time.Hour).Err()
			case "projection_ttl":
				err = r.Expire(ctx, ownershipProjectionKey, time.Hour).Err()
			case "route_epoch":
				err = r.HSet(ctx, p.target.keys()[0], "routing_epoch", p.plan.Epoch()-1).Err()
			case "route_ttl":
				err = r.Expire(ctx, p.target.keys()[0], time.Hour).Err()
			case "producer_ttl":
				err = r.Expire(ctx, "lightpanda-b0:producer-owner", time.Hour).Err()
			case "producer_cohort":
				err = r.HSet(ctx, "lightpanda-b0:producer-owner", "cohort", "c2").Err()
			case "selector":
				err = r.HDel(ctx, "lightpanda-b0:producer-owner", "board_slug:browser-use-careers").Err()
			case "record":
				err = r.HSet(ctx, p.target.keys()[1], "not-a-task", "{}").Err()
			case "canonical_disabled":
				_, err = p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.target.document.Boards[0].ID)
			case "redis_config":
				err = r.HSet(ctx, "board:"+p.target.document.Boards[0].ID, "check_interval_minutes", "61").Err()
			case "target_missing":
				_, err = p.f.observer.Exec(ctx, "TRUNCATE crawler_ownership_b0_target")
			case "journal_phase":
				_, err = p.f.observer.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='superseded' WHERE intent_sha256=$1", p.intent)
			}
			if err != nil {
				t.Fatal("private authority fault could not be installed")
			}
			before := snapshot(t, p.f.client)
			canonical := coldCanonicalSnapshot(t, p.f)
			if _, err := a.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("fault admitted claim", mode, err)
			}
			if err := a.AttestOwnershipProjection(ctx, p.plan.ProjectionSHA1()); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("fault admitted live identity", mode, err)
			}
			if _, err := OpenJointOwnedAuthority(ctx, p.f.dsn, p.f.client, p.plan.Epoch(), p.plan.digest, p.spec.SourceRevision, []byte(p.target.lua)); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("fault admitted restarted owner", mode, err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("rejected admission changed queue/config/canonical state")
			}
		})
	}
}

func TestRealJointAdmissionAllowsConservedB0InflightAndFencesExistingClaim(t *testing.T) {
	p := realPublication(t)
	a := activateJointFixture(t, p)
	ctx := context.Background()
	args := p.target.auditArguments(p.plan.Epoch())
	args[0] = "claim_next"
	args[7] = "600000"
	now, err := p.f.client.redis.Time(ctx).Result()
	if err != nil {
		t.Fatal(err)
	}
	args[6] = strconv.FormatInt(now.UnixMilli(), 10)
	reply, err := p.f.client.redis.Eval(ctx, p.target.lua, p.target.keys(), args...).Slice()
	if err != nil || len(reply) != 12 || reply[0] != "accepted" || p.f.client.redis.ZCard(ctx, p.target.keys()[3]).Val() != 1 {
		t.Fatal("actual B0 inflight fixture unavailable", err, reply)
	}
	if err := a.AttestOwnershipProjection(ctx, p.plan.ProjectionSHA1()); err != nil {
		t.Fatal("healthy inflight B0 work rejected", err)
	}
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil {
		t.Fatal("joint ordinary claim unavailable", err)
	}
	if err := a.Heartbeat(ctx, claim); err != nil {
		t.Fatal(err)
	}
	// Record a real completed receipt with a future canonical due, then corrupt
	// the joint marker. Settlement cannot erase the still-authoritative lease.
	receipt, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_board SET next_check_at=now()+interval '1 hour' WHERE id=$1::uuid", claim.boardID)
		return err
	})
	if err != nil || receipt == nil {
		t.Fatal("real joint receipt unavailable", err)
	}
	if err := p.f.client.redis.Del(ctx, coldPublicationKey).Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, p.f.client)
	canonical := coldCanonicalSnapshot(t, p.f)
	called := false
	if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("heartbeat extended lease after joint witness loss")
	}
	if _, err := a.Write(ctx, claim, false, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
		t.Fatal("canonical callback ran after joint witness loss")
	}
	if err := a.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("settlement retired lease after joint witness loss")
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) {
		t.Fatal("witness loss replayed/changed receipt, future deadline or queue")
	}
}
