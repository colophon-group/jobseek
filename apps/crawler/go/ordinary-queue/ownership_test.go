package queue

import (
	"context"
	"crypto/sha1"
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
	"github.com/redis/go-redis/v9"
)

func TestOwnershipSchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/ordinary_worker_ownership.sql")
	if err != nil || string(body) != ownershipSchema {
		t.Fatal("ownership schema differs from actual migration")
	}
}

func testOwnershipBody(t *testing.T, doc ownershipDocument) (string, string) {
	t.Helper()
	body, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	hash := sha256.Sum256(body)
	return string(body), hex.EncodeToString(hash[:])
}

func testOwnershipDocument(t *testing.T) ownershipDocument {
	t.Helper()
	config := profileConfig()
	profile, err := InspectGreenhouseMonitor(profileBoardID, config)
	if err != nil {
		t.Fatal(err)
	}
	return ownershipDocument{ownershipVersion, 7, strings.Repeat("a", 40), []ownershipMember{{profileBoardID, profile.CompanyID, profile.Domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, config}}, ownershipProjectionVersion, nil}
}

func TestOwnershipPayloadRequiresCanonicalIdentityAndSupportedMembers(t *testing.T) {
	doc := testOwnershipDocument(t)
	body, digest := testOwnershipBody(t, doc)
	plan, err := decodeOwnership(body, digest)
	if err != nil || plan.SHA256() != digest || plan.Epoch() != 7 || plan.MemberCount() != 1 || plan.SourceRevision() != doc.SourceRevision {
		t.Fatal("valid ownership payload rejected")
	}
	for _, change := range []func(*ownershipDocument){
		func(d *ownershipDocument) { d.Version = "unknown" },
		func(d *ownershipDocument) { d.ProjectionVersion = "unknown" },
		func(d *ownershipDocument) { d.Epoch = 0 },
		func(d *ownershipDocument) { d.SourceRevision = "not-a-revision" },
		func(d *ownershipDocument) { d.Members = nil },
		func(d *ownershipDocument) { d.Members = append(d.Members, d.Members[0]) },
		func(d *ownershipDocument) { d.Members[0].Profile = "unknown" },
		func(d *ownershipDocument) { d.Members[0].Worker = Browser },
		func(d *ownershipDocument) { d.Members[0].Kind = Scrape },
		func(d *ownershipDocument) { d.Members[0].Domain = "changed" },
		func(d *ownershipDocument) { d.Members[0].EffectiveConfigHash = strings.Repeat("b", 64) },
		func(d *ownershipDocument) {
			d.Members[0].Config["metadata"] = `{"token":"changed","scraper_type":"skip"}`
		},
	} {
		bad := testOwnershipDocument(t)
		change(&bad)
		body, digest := testOwnershipBody(t, bad)
		if p, err := decodeOwnership(body, digest); p != nil || !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("invalid member granted an ownership document")
		}
	}
	for _, bad := range []string{body + " ", strings.Replace(body, `"routing_epoch":7`, `"routing_epoch":8,"routing_epoch":7`, 1), strings.Replace(body, `"members":`, `"unreviewed":"secret","members":`, 1)} {
		hash := sha256.Sum256([]byte(bad))
		if p, err := decodeOwnership(bad, hex.EncodeToString(hash[:])); p != nil || !errors.Is(err, ErrAuthorityLost) || strings.Contains(err.Error(), "secret") {
			t.Fatal("noncanonical payload accepted or leaked")
		}
	}
	if _, err := decodeOwnership(body, strings.Repeat("0", 64)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("unbound digest accepted")
	}
}

func stageFixturePlan(t *testing.T, f authorityFixture, revision string) *OwnershipPlan {
	t.Helper()
	p, err := f.authority.StageGreenhouseOwnership(context.Background(), revision, []string{f.task.ID})
	if err != nil || p == nil {
		t.Fatalf("real canonical staging failed: %v", err)
	}
	t.Cleanup(func() {
		_, err := f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest)
		if err != nil {
			t.Error("owned plan cleanup retirement failed")
		}
	})
	return p
}

func activateFixturePlan(t *testing.T, f authorityFixture, p *OwnershipPlan) {
	t.Helper()
	// Private SQL fixture only. Production activation has no endpoint here and
	// still requires the supported full quiesced revision/projection transaction.
	if _, err := f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", p.digest); err != nil {
		t.Fatal("private plan activation failed")
	}
}

func TestRealOwnershipStagingAndExactActiveLoad(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	before := snapshot(t, f.client)
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	if p.Epoch() != f.epoch || p.MemberCount() != 1 || !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("staging changed queue authority")
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, p.digest, p.SourceRevision()); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("staged plan treated as active")
	}
	repeat, err := f.authority.StageGreenhouseOwnership(ctx, p.SourceRevision(), []string{f.task.ID})
	if err != nil || repeat.digest != p.digest {
		t.Fatal("staged capture is not idempotent")
	}
	activateFixturePlan(t, f, p)
	loaded, err := f.authority.LoadActiveOwnership(ctx, p.digest, p.SourceRevision())
	if err != nil || loaded.digest != p.digest || loaded.body != p.body {
		t.Fatal("exact active plan not retained")
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, p.digest, strings.Repeat("b", 40)); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("wrong revision admitted")
	}
	if _, err := f.authority.StageGreenhouseOwnership(ctx, p.SourceRevision(), []string{f.task.ID}); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("active plan was returned as staged")
	}
	for _, sql := range []string{
		"UPDATE ordinary_worker_ownership_plan SET state='staged' WHERE plan_sha256=$1",
		"UPDATE ordinary_worker_ownership_plan SET source_revision=repeat('b',40) WHERE plan_sha256=$1",
		"UPDATE ordinary_worker_ownership_plan SET payload=payload||' ' WHERE plan_sha256=$1",
		"DELETE FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1",
	} {
		if _, err := f.observer.Exec(ctx, sql, p.digest); err == nil {
			t.Fatal("active ownership identity/history mutated")
		}
	}
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1", p.digest); err != nil {
		t.Fatal(err)
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, p.digest, p.SourceRevision()); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired plan loaded")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", p.digest); err == nil {
		t.Fatal("retired plan restored without a new identity")
	}
}

func TestRealOwnershipBlocksUnboundClaimsWritesHeartbeatAndSettlement(t *testing.T) {
	for _, terminal := range []bool{false, true} {
		t.Run(map[bool]string{false: "active_write", true: "terminal_settle"}[terminal], func(t *testing.T) {
			f := greenhouseAuthorityFixture(t)
			ctx := context.Background()
			claim := mustClaim(t, f)
			var receipt *Receipt
			if terminal {
				var err error
				receipt, err = f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error { return effect(ctx, tx, claim, futureDue()) })
				if err != nil {
					t.Fatal(err)
				}
			}
			p := stageFixturePlan(t, f, strings.Repeat("a", 40))
			activateFixturePlan(t, f, p)
			// A second due entry ensures the rejected claim proves no queue pop.
			if err := f.client.redis.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: 1, Member: "00000000-0000-4000-8000-000000000002"}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: 1, Member: "greenhouse"}).Err(); err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, f.client)
			if got, err := f.authority.Claim(ctx, Simple); got != nil || !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unbound authority claimed under active ownership")
			}
			called := false
			if _, err := f.authority.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); called || !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unbound callback wrote under active ownership")
			}
			if err := f.authority.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unbound heartbeat extended authority")
			}
			if terminal {
				if err := f.authority.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) {
					t.Fatal("unbound terminal settled")
				}
			}
			if !reflect.DeepEqual(before, snapshot(t, f.client)) {
				t.Fatal("rejected unbound operations mutated Redis")
			}
			assertCanonical(t, f, terminal)
		})
	}
}

func TestRealOwnershipRejectsPartialStagingAndMalformedSQLPayload(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	before := snapshot(t, f.client)
	if p, err := f.authority.StageGreenhouseOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID, "00000000-0000-4000-8000-000000000002"}); p != nil || !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatalf("partial canonical inventory staged: %v", err)
	}
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_ownership_plan WHERE routing_epoch=$1", f.epoch).Scan(&count); err != nil || count != 0 || !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("failed staging changed durable ownership or queue state")
	}
	doc := testOwnershipDocument(t)
	doc.Epoch = f.epoch
	body, _ := testOwnershipBody(t, doc)
	var object map[string]any
	if err := json.Unmarshal([]byte(body), &object); err != nil {
		t.Fatal(err)
	}
	for _, field := range []string{"version", "routing_epoch", "source_revision", "members"} {
		original := object[field]
		delete(object, field)
		bad, err := json.Marshal(object)
		if err != nil {
			t.Fatal(err)
		}
		hash := sha256.Sum256(bad)
		if _, err := f.observer.Exec(ctx, "INSERT INTO ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)", hex.EncodeToString(hash[:]), f.epoch, doc.SourceRevision, string(bad)); err == nil {
			t.Fatalf("SQL accepted missing %s through nullable CHECK", field)
		}
		object[field] = original
	}
	if _, err := f.observer.Exec(ctx, "INSERT INTO ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)", strings.Repeat("0", 64), f.epoch, doc.SourceRevision, body); err == nil {
		t.Fatal("SQL accepted payload with mismatched digest")
	}
}

func TestRealOwnershipActivationWaitsForOrdinaryLeaseBarrier(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
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
	go func() {
		_, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", p.digest)
		done <- err
	}()
	for {
		select {
		case err := <-done:
			t.Fatalf("activation crossed active ordinary transaction: %v", err)
		default:
		}
		var waiting bool
		if err := f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1::integer=ANY(pg_blocking_pids(pid)))", pid).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("activation never reached lease barrier")
		}
		time.Sleep(5 * time.Millisecond)
	}
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err := awaitWrite(t, done); err != nil {
		t.Fatalf("activation failed after barrier release: %v", err)
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, p.digest, p.SourceRevision()); err != nil {
		t.Fatal("activation lost exact durable identity")
	}
}

func TestRealOwnershipReadDoesNotInvertTransitionRowAndLeaseLocks(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	activateFixturePlan(t, f, p)
	done := make(chan error, 1)
	retirementContext := ctx
	err := f.authority.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		var pid int
		if err := tx.QueryRow(ctx, "SELECT pg_backend_pid()").Scan(&pid); err != nil {
			return err
		}
		// The transition already holds this plan's row when its trigger waits
		// for our shared barrier. Readback must not request a conflicting row lock.
		go func() {
			_, err := f.observer.Exec(retirementContext, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1", p.digest)
			done <- err
		}()
		for {
			select {
			case err := <-done:
				t.Fatalf("retirement crossed active readback barrier: %v", err)
			default:
			}
			var waiting bool
			if err := f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE $1::integer=ANY(pg_blocking_pids(pid)))", pid).Scan(&waiting); err != nil {
				return err
			}
			if waiting {
				break
			}
			if ctx.Err() != nil {
				return ctx.Err()
			}
			time.Sleep(5 * time.Millisecond)
		}
		loaded, err := f.authority.loadActiveOwnership(ctx, tx, p.digest, p.SourceRevision())
		if err != nil {
			return err
		}
		if loaded.digest != p.digest {
			return ErrAuthorityLost
		}
		return nil
	})
	if err != nil {
		t.Fatalf("readback inverted transition row/barrier order: %v", err)
	}
	if err := awaitWrite(t, done); err != nil {
		t.Fatalf("retirement failed after readback: %v", err)
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, p.digest, p.SourceRevision()); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired plan retained readback authority")
	}
}

func TestRealOwnershipOnlyOneActiveAndNoStaleActivation(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	first := stageFixturePlan(t, f, strings.Repeat("a", 40))
	second := stageFixturePlan(t, f, strings.Repeat("b", 40))
	activateFixturePlan(t, f, first)
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", second.digest); err == nil {
		t.Fatal("multiple active plans admitted")
	}
	if _, err := f.observer.Exec(ctx, "SELECT nextval('lightpanda_b0_routing_epoch_seq')"); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1", first.digest); err != nil {
		t.Fatal("could not retire old plan")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", second.digest); err == nil {
		t.Fatal("retired epoch activated")
	}
	if _, err := f.authority.LoadActiveOwnership(ctx, first.digest, first.SourceRevision()); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("old epoch loader retained authority")
	}
}

func TestOwnershipProjectionRetainsFullPlanBindingWithoutConfigurations(t *testing.T) {
	doc := testOwnershipDocument(t)
	body, digest := testOwnershipBody(t, doc)
	plan, err := decodeOwnership(body, digest)
	if err != nil {
		t.Fatal(err)
	}
	var routing ownershipProjectionDocument
	if json.Unmarshal([]byte(plan.projection), &routing) != nil || routing.PlanSHA256 != digest || routing.Epoch != doc.Epoch || routing.SourceRevision != doc.SourceRevision || routing.Version != ownershipProjectionVersion || len(routing.Members) != 1 || routing.Members[doc.Members[0].BoardID] != doc.Members[0].Domain {
		t.Fatal("projection lost immutable plan or membership binding")
	}
	if strings.Contains(plan.projection, `"config"`) || strings.Contains(plan.projection, `"metadata"`) || len(plan.projection) >= len(body) {
		t.Fatal("claim projection still carries full configurations")
	}
	doc.Members[0].Config["metadata"] = `{"token":"changed","scraper_type":"skip"}`
	profile, err := InspectRichMonitor(doc.Members[0].BoardID, doc.Members[0].Config)
	if err != nil {
		t.Fatal(err)
	}
	doc.Members[0].EffectiveConfigHash = profile.EffectiveConfigSHA256
	changedBody, changedDigest := testOwnershipBody(t, doc)
	changed, err := decodeOwnership(changedBody, changedDigest)
	if err != nil || changed.projection == plan.projection || changed.ProjectionSHA1() == plan.ProjectionSHA1() {
		t.Fatal("configuration change did not invalidate routing projection binding")
	}
}

func TestOwnershipProjectionMatchesActualPythonCodec(t *testing.T) {
	body, err := os.ReadFile("testdata/ownership_projection_python.json")
	if err != nil {
		t.Fatal(err)
	}
	var capture struct {
		Cases []struct {
			Name, Payload, Projection string
			Digest                    string `json:"plan_sha256"`
			Hash                      string `json:"projection_sha1"`
		}
	}
	if json.Unmarshal(body, &capture) != nil || len(capture.Cases) != 6 {
		t.Fatal("Python projection capture unavailable")
	}
	for _, c := range capture.Cases {
		var doc ownershipDocument
		if json.Unmarshal([]byte(c.Payload), &doc) != nil {
			t.Fatal("invalid capture")
		}
		routing := ownershipProjectionDocument{Version: ownershipProjectionVersion, Epoch: doc.Epoch, SourceRevision: doc.SourceRevision, PlanSHA256: c.Digest, Members: make(map[string]string)}
		for _, member := range doc.Members {
			routing.Members[member.BoardID] = member.Domain
		}
		if len(doc.Details) > 0 {
			routing.Details = make(map[string]string, len(doc.Details))
			for _, detail := range doc.Details {
				routing.Details[detail.BoardID] = detail.Domain
			}
		}
		projected, err := json.Marshal(routing)
		hash := sha1.Sum(projected)
		if err != nil || string(projected) != c.Projection || hex.EncodeToString(hash[:]) != c.Hash {
			t.Fatal("Go routing bytes differ from actual Python codec", c.Name)
		}
	}
}
