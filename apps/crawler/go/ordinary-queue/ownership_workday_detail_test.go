package queue

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
	"reflect"
	"strings"
	"testing"
	"time"
)

func workdayOwnershipDocument(t *testing.T) ownershipDocument {
	t.Helper()
	doc := testOwnershipDocument(t)
	config := workdayDetailConfig()
	config["domain"], config["throttle_key"] = "workday", "workday"
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil {
		t.Fatal(err)
	}
	doc.Members = []ownershipMember{{profileBoardID, p.CompanyID, p.Domain, Monitor, Simple, p.Profile, p.EffectiveConfigSHA256, config}}
	doc.Details = []ownershipDetail{{BoardID: profileBoardID, Domain: "fixture.wd1.myworkdayjobs.com", Profile: "workday.cxs-detail/v1", Worker: Simple}}
	return doc
}

func TestRealWorkdayDetailOwnedClaimExcludesLegacyAndCommitsCanonicalReceipt(t *testing.T) {
	f := currentWorkdayDetailQueueFixture(t)
	ctx := context.Background()
	p, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	activateFixturePlan(t, f, p)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest)
	})
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	before := snapshot(t, f.client)
	if task, err := f.client.ClaimLegacyBound(ctx, Simple, p); err != nil || task != nil {
		t.Fatal("legacy claimed native detail", err)
	}
	// Legacy fairness cursors may advance; its owned queue/lease stay unchanged.
	if f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("legacy mutated native lease")
	}
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.task.Kind != Scrape || !claim.OwnershipBound() {
		t.Fatal("actual native detail pop failed", err)
	}
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	receipt, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Owned native'],next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
		return err
	})
	if err != nil || receipt == nil {
		t.Fatal("owned detail write failed", err)
	}
	if err := a.Settle(ctx, claim, receipt); err != nil {
		t.Fatal(err)
	}
	if reflect.DeepEqual(before, snapshot(t, f.client)) || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("native did not settle original queue")
	}
}

func TestRealWorkdayDetailOwnedCandidateRejectsForgedPostingRouteBeforePop(t *testing.T) {
	f := currentWorkdayDetailQueueFixture(t)
	ctx := context.Background()
	p, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	activateFixturePlan(t, f, p)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest)
	})
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=source_url||'/changed' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, f.client)
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim != nil || !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("forged source popped or mutated queue", err)
	}
}

func TestWorkdayDetailOwnershipRequiresExplicitEligibleBoardSubset(t *testing.T) {
	doc := workdayOwnershipDocument(t)
	body, digest := testOwnershipBody(t, doc)
	plan, err := decodeOwnership(body, digest)
	if err != nil {
		t.Fatal(err)
	}
	var projection ownershipProjectionDocument
	if json.Unmarshal([]byte(plan.ProjectionJSON()), &projection) != nil || projection.Members[profileBoardID] != "workday" || projection.Details[profileBoardID] != "fixture.wd1.myworkdayjobs.com" {
		t.Fatal("monitor and actual detail domain binding lost")
	}
	for name, change := range map[string]func(*ownershipDocument){
		"foreign_board":       func(d *ownershipDocument) { d.Details[0].BoardID = "00000000-0000-4000-8000-000000000099" },
		"duplicate":           func(d *ownershipDocument) { d.Details = append(d.Details, d.Details[0]) },
		"wrong_domain":        func(d *ownershipDocument) { d.Details[0].Domain = "workday" },
		"wrong_profile":       func(d *ownershipDocument) { d.Details[0].Profile = "workday.cxs-urls/v1" },
		"browser":             func(d *ownershipDocument) { d.Details[0].Worker = Browser },
		"retained_projection": func(d *ownershipDocument) { d.ProjectionVersion = "" },
		"other_scraper": func(d *ownershipDocument) {
			d.Members[0].Config["metadata"] = `{"scraper_type":"json-ld"}`
			p, err := InspectRichMonitor(profileBoardID, d.Members[0].Config)
			if err != nil {
				t.Fatal(err)
			}
			d.Members[0].EffectiveConfigHash = p.EffectiveConfigSHA256
		},
	} {
		t.Run(name, func(t *testing.T) {
			bad := workdayOwnershipDocument(t)
			change(&bad)
			body, hash := testOwnershipBody(t, bad)
			if _, err := decodeOwnership(body, hash); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("invalid detail membership admitted", err)
			}
		})
	}
	// Existing monitor owners keep byte-identical retained identities: adding a
	// decoder field must never change their canonical JSON or retirement hash.
	doc.Details = nil
	body, digest = testOwnershipBody(t, doc)
	plan, err = decodeOwnership(body, digest)
	if err != nil || strings.Contains(body, `"details"`) || strings.Contains(plan.ProjectionJSON(), `"details"`) {
		t.Fatal("monitor-only retained identity changed", err)
	}
}

func TestWorkdayDetailOwnershipAdmissionMatchesSupportedMonitorSites(t *testing.T) {
	for _, site := range []string{"Careers", "Career-Site", "Career_Site"} {
		config := workdayDetailConfig()
		config["board_url"] = "https://fixture.wd1.myworkdayjobs.com/en-US/" + site
		p, err := inspectWorkdayDetailOwnership(profileBoardID, config)
		if err != nil || p.Domain != "fixture.wd1.myworkdayjobs.com" {
			t.Fatal("site admission differs", site, err)
		}
	}
}

func TestRealDetailOnlyClaimLeavesDueMonitorQueued(t *testing.T) {
	f := currentWorkdayDetailQueueFixture(t)
	ctx := context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=clock_timestamp()-interval '1 hour' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"monitors_simple:workday", "ready:simple:1"} {
		member := f.task.ID
		if key == "ready:simple:1" {
			member = "workday"
		}
		if err := f.client.redis.ZAdd(ctx, key, redis.Z{Score: 1, Member: member}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	// An eligible first-time detail precedes a recurring monitor under the
	// unchanged global queue priority. Capacity selection must retain that
	// priority while leaving the due monitor untouched.
	domain, err := f.client.redis.HGet(ctx, "scrape:"+f.task.ID, "domain").Result()
	if err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZRem(ctx, "scrapes_simple:"+domain, f.task.ID).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZAdd(ctx, "ft_scrapes_simple:"+domain, redis.Z{Score: 1, Member: f.task.ID}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZAdd(ctx, "ready:simple:0", redis.Z{Score: 1, Member: domain}).Err(); err != nil {
		t.Fatal(err)
	}
	p, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	activateFixturePlan(t, f, p)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest)
	})
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	claim, err := a.ClaimDetails(ctx, Simple)
	if err != nil || claim == nil || claim.Descriptor().Kind != Scrape || !claim.OwnershipBound() {
		t.Fatal("detail-only claim unavailable", err)
	}
	if score := f.client.redis.ZScore(ctx, "monitors_simple:workday", f.task.ID).Val(); score != 1 {
		t.Fatal("detail claim consumed due monitor")
	}
	var checked *time.Time
	if err := f.observer.QueryRow(ctx, "SELECT last_checked_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&checked); err != nil || checked != nil {
		t.Fatal("detail-only claim changed monitor freshness", err)
	}
	current, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	due := futureDue()
	receipt, err := a.WriteWorkdayDetail(ctx, current, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=$2 WHERE id=$1::uuid", claim.Descriptor().ID, due)
		return err
	})
	if err != nil || receipt == nil {
		t.Fatal("canonical detail receipt missing", err)
	}
	if err := a.Settle(ctx, claim, receipt); err != nil {
		t.Fatal("detail-only settlement failed", err)
	}
	monitor, err := a.Claim(ctx, Simple)
	if err != nil || monitor == nil || monitor.Descriptor().Kind != Monitor {
		t.Fatal("retained due monitor not claimable", err)
	}
}

func TestRealDetailOnlyClaimPreservesFirstTimeMonitorPriority(t *testing.T) {
	f := currentWorkdayDetailQueueFixture(t)
	ctx := context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=clock_timestamp()-interval '1 hour' WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"ft_monitors_simple:workday", "ready:simple:0"} {
		member := f.task.ID
		if key == "ready:simple:0" {
			member = "workday"
		}
		if err := f.client.redis.ZAdd(ctx, key, redis.Z{Score: 1, Member: member}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	p, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	activateFixturePlan(t, f, p)
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest)
	})
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	before := snapshot(t, f.client)
	claim, err := a.ClaimDetails(ctx, Simple)
	if err != nil || claim != nil {
		t.Fatal("capacity selection bypassed strict first-time monitor priority", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("priority refusal changed retained queue state")
	}
	var checked *time.Time
	if err := f.observer.QueryRow(ctx, "SELECT last_checked_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&checked); err != nil || checked != nil {
		t.Fatal("priority refusal fabricated monitor freshness", err)
	}
}
