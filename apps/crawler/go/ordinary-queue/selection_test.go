package queue

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func privateOwnershipProjection(t *testing.T, c *Client, doc ownershipDocument) *OwnershipPlan {
	t.Helper()
	body, digest := testOwnershipBody(t, doc)
	p, err := decodeOwnership(body, digest)
	if err != nil {
		t.Fatal(err)
	}
	if err := c.redis.Set(context.Background(), ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	return p
}

func nativeQueueClaim(t *testing.T, c *Client, p *OwnershipPlan, id string, config map[string]string) (*Task, error) {
	t.Helper()
	body, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	return c.claimTaskBound(context.Background(), Simple, strings.Repeat("a", 32), p.claimBinding("native", id, string(body)))
}

func addQueuedMonitor(t *testing.T, c *Client, id, domain string, score float64, first bool) {
	t.Helper()
	ctx := context.Background()
	prefix, tier := "monitors_simple:", "1"
	if first {
		prefix, tier = "ft_monitors_simple:", "0"
	}
	if err := c.redis.HSet(ctx, "board:"+id, map[string]string{"domain": domain, "crawler_type": "fixture"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, prefix+domain, redis.Z{Score: score, Member: id}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ready:simple:"+tier, redis.Z{Score: score, Member: domain}).Err(); err != nil {
		t.Fatal(err)
	}
}

func TestInstalledOwnershipLegacyScansPastSelectedHeads(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	doc := testOwnershipDocument(t)
	doc.Members = nil
	for i := 1; i <= 80; i++ {
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
		config := profileConfig()
		profile, err := InspectGreenhouseMonitor(id, config)
		if err != nil {
			t.Fatal(err)
		}
		doc.Members = append(doc.Members, ownershipMember{id, profile.CompanyID, profile.Domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, config})
		addQueuedMonitor(t, c, id, "greenhouse", float64(i), false)
	}
	p := privateOwnershipProjection(t, c, doc)
	foreign := "00000000-0000-4000-8000-000000000099"
	addQueuedMonitor(t, c, foreign, "greenhouse", 100, false)
	if task, err := c.ClaimLegacyBound(ctx, Simple, p); err != nil || task != nil {
		t.Fatalf("bounded first scan changed owner: %v", err)
	}
	task, err := c.ClaimLegacyBound(ctx, Simple, p)
	if err != nil || task == nil || task.ID != foreign {
		t.Fatalf("legacy scan stranded a foreign head: %v", err)
	}
	for i, member := range doc.Members {
		score, err := c.redis.ZScore(ctx, "monitors_simple:greenhouse", member.BoardID).Result()
		if err != nil || score != float64(i+1) {
			t.Fatal("legacy modified selected membership or score")
		}
	}
}

func TestInstalledOwnershipBothOwnersProgressBeyondDomainWindow(t *testing.T) {
	c := privateRedis(t)
	c.settings.MaxDomains = 1
	ctx := context.Background()
	doc := testOwnershipDocument(t)
	doc.Members = nil
	for i := 1; i <= 5; i++ {
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
		domain := fmt.Sprintf("domain-%02d", i)
		config := profileConfig()
		config["domain"] = domain
		config["throttle_key"] = domain
		profile, err := InspectGreenhouseMonitor(id, config)
		if err != nil {
			t.Fatal(err)
		}
		doc.Members = append(doc.Members, ownershipMember{id, profile.CompanyID, domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, config})
		addQueuedMonitor(t, c, id, domain, float64(i), false)
		if err := c.redis.HSet(ctx, "board:"+id, config).Err(); err != nil {
			t.Fatal(err)
		}
	}
	p := privateOwnershipProjection(t, c, doc)
	foreign := "00000000-0000-4000-8000-000000000099"
	addQueuedMonitor(t, c, foreign, "domain-99", 10, false)
	last := doc.Members[4]
	native, err := nativeQueueClaim(t, c, p, last.BoardID, last.Config)
	if err != nil || native == nil || native.ID != last.BoardID {
		t.Fatalf("native stranded behind foreign domain window: %v", err)
	}
	var legacy *Task
	for i := 0; i < 6; i++ {
		legacy, err = c.ClaimLegacyBound(ctx, Simple, p)
		if err != nil {
			t.Fatal(err)
		}
		if legacy != nil {
			break
		}
	}
	if legacy == nil || legacy.ID != foreign {
		t.Fatal("legacy domain cursor never reached its work")
	}
	for _, member := range doc.Members[:4] {
		if _, err := c.redis.ZScore(ctx, "monitors_simple:"+member.Domain, member.BoardID).Result(); err != nil {
			t.Fatal("legacy popped selected foreign domain work")
		}
	}
}

func TestInstalledOwnershipPreservesGlobalFirstTimeAndFairness(t *testing.T) {
	for _, fairness := range []bool{false, true} {
		t.Run(fmt.Sprint(fairness), func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			doc := testOwnershipDocument(t)
			member := doc.Members[0]
			p := privateOwnershipProjection(t, c, doc)
			addQueuedMonitor(t, c, member.BoardID, member.Domain, 1, false)
			if err := c.redis.HSet(ctx, "board:"+member.BoardID, member.Config).Err(); err != nil {
				t.Fatal(err)
			}
			foreign := "00000000-0000-4000-8000-000000000099"
			if !fairness {
				addQueuedMonitor(t, c, foreign, member.Domain, 1, true)
			} else {
				if err := c.redis.Set(ctx, "claim:recurring-monitor-streak:simple", "8", 0).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "scrapes_simple:"+member.Domain, redis.Z{Score: 1, Member: foreign}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.ZAdd(ctx, "ready:simple:2", redis.Z{Score: 1, Member: member.Domain}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.HSet(ctx, "scrape:"+foreign, "domain", member.Domain).Err(); err != nil {
					t.Fatal(err)
				}
			}
			before := snapshot(t, c)
			if task, err := nativeQueueClaim(t, c, p, member.BoardID, member.Config); err != nil || task != nil || !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatalf("native bypassed global priority: %v", err)
			}
			legacy, err := c.ClaimLegacyBound(ctx, Simple, p)
			if err != nil || legacy == nil || legacy.ID != foreign {
				t.Fatalf("priority work unavailable to its owner: %v", err)
			}
			if _, err := c.Complete(ctx, legacy); err != nil {
				t.Fatal(err)
			}
			native, err := nativeQueueClaim(t, c, p, member.BoardID, member.Config)
			if err != nil || native == nil || native.ID != member.BoardID {
				t.Fatalf("native work stranded after priority drains: %v", err)
			}
		})
	}
}

func TestInstalledOwnershipRejectsProjectionConfigAndIndexLossBeforeEffects(t *testing.T) {
	for _, mode := range []string{"missing", "corrupt", "wrongtype", "full_loss", "config", "delay", "cursor", "queue_type", "unaware"} {
		t.Run(mode, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			doc := testOwnershipDocument(t)
			member := doc.Members[0]
			p := privateOwnershipProjection(t, c, doc)
			addQueuedMonitor(t, c, member.BoardID, member.Domain, 1, false)
			if err := c.redis.HSet(ctx, "board:"+member.BoardID, member.Config).Err(); err != nil {
				t.Fatal(err)
			}
			var err error
			switch mode {
			case "missing":
				err = c.redis.Del(ctx, ownershipProjectionKey).Err()
			case "corrupt":
				err = c.redis.Set(ctx, ownershipProjectionKey, p.projection+" ", 0).Err()
			case "wrongtype":
				err = c.redis.Del(ctx, ownershipProjectionKey).Err()
				if err == nil {
					err = c.redis.HSet(ctx, ownershipProjectionKey, "bad", "secret").Err()
				}
			case "full_loss":
				err = c.redis.FlushDB(ctx).Err()
				if err == nil {
					addQueuedMonitor(t, c, member.BoardID, member.Domain, 1, false)
				}
			case "config":
				err = c.redis.HSet(ctx, "board:"+member.BoardID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Err()
			case "delay":
				err = c.redis.Set(ctx, "delay:"+member.Domain, "1e300", 0).Err()
			case "cursor":
				err = c.redis.Set(ctx, "ordinary:claim-cursor:"+p.digest+":simple:domains:1", "secret", 0).Err()
			case "queue_type":
				err = c.redis.Set(ctx, "ft_scrapes_simple:"+member.Domain, "secret", 0).Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, c)
			if mode == "unaware" {
				task, err := c.Claim(ctx, Simple)
				if task != nil || err == nil {
					t.Fatal("unaware caller bypassed ownership")
				}
			} else if mode == "cursor" {
				task, err := c.ClaimLegacyBound(ctx, Simple, p)
				if task != nil || err == nil {
					t.Fatal("corrupt cursor changed queue")
				}
			} else {
				task, err := nativeQueueClaim(t, c, p, member.BoardID, member.Config)
				if task != nil || err == nil || strings.Contains(err.Error(), "secret") {
					t.Fatal("corrupt native state changed queue or leaked")
				}
				if mode != "config" {
					task, err = c.ClaimLegacyBound(ctx, Simple, p)
					if task != nil || err == nil {
						t.Fatal("planned legacy caller bypassed corrupt ownership")
					}
				}
			}
			if !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("rejected ownership mutated Redis before pop")
			}
		})
	}
}

func realOwnedAuthority(t *testing.T) (authorityFixture, *Authority, *OwnershipPlan) {
	t.Helper()
	f := greenhouseAuthorityFixture(t)
	p := stageFixturePlan(t, f, strings.Repeat("a", 40))
	activateFixturePlan(t, f, p)
	if err := f.client.redis.Set(context.Background(), ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(context.Background(), f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	return f, a, p
}

func TestRealOwnedClaimWriteAndSettlementPreserveForeignHead(t *testing.T) {
	f, a, _ := realOwnedAuthority(t)
	ctx := context.Background()
	foreign := "00000000-0000-4000-8000-000000000099"
	addQueuedMonitor(t, f.client, foreign, "greenhouse", 1, false)
	foreignConfig, err := f.client.redis.HGetAll(ctx, "board:"+foreign).Result()
	if err != nil {
		t.Fatal(err)
	}
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != f.task.ID {
		t.Fatalf("bound canonical claim failed: %v", err)
	}
	due := futureDue()
	receipt, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error { return effect(ctx, tx, claim, due) })
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Settle(ctx, claim, receipt); err != nil {
		t.Fatal(err)
	}
	assertCanonical(t, f, true)
	if score, err := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", foreign).Result(); err != nil || score != 1 {
		t.Fatal("native modified foreign queue score")
	}
	after, err := f.client.redis.HGetAll(ctx, "board:"+foreign).Result()
	if err != nil || !reflect.DeepEqual(foreignConfig, after) {
		t.Fatal("native modified foreign config")
	}
	if score, err := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", f.task.ID).Result(); err != nil || score != seconds(due) {
		t.Fatal("bound settlement lost canonical schedule")
	}
}

func TestRealOwnedClaimContinuesAfterOlderRateLimitedDomain(t *testing.T) {
	f := greenhouseAuthorityFixture(t)
	ctx := context.Background()
	second := ordinaryID(t)
	config, err := f.client.redis.HGetAll(ctx, "board:"+f.task.ID).Result()
	if err != nil {
		t.Fatal(err)
	}
	config["crawler_type"], config["domain"], config["throttle_key"] = "ashby", "ashby", "ashby"
	config["board_url"], config["board_slug"] = "https://jobs.ashbyhq.com/"+second, "ashby-"+second
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_board
  (id,company_id,board_slug,board_url,crawler_type,metadata,check_interval_minutes,scrape_interval_hours,throttle_key,monitor_needs_browser,scraper_needs_browser,is_enabled,board_status)
  SELECT $2::uuid,company_id,$3,$4,'ashby',metadata,check_interval_minutes,scrape_interval_hours,'ashby',false,false,true,'active'
  FROM job_board WHERE id=$1::uuid`, f.task.ID, second, config["board_slug"], config["board_url"]); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", second); err != nil {
			t.Error("second-provider fixture cleanup failed")
		}
	})
	if err := f.client.redis.HSet(ctx, "board:"+second, config).Err(); err != nil {
		t.Fatal(err)
	}
	for domain, id := range map[string]string{"greenhouse": f.task.ID, "ashby": second} {
		score := float64(1)
		if domain == "ashby" {
			score = 2
		}
		if err := f.client.redis.ZAdd(ctx, "monitors_simple:"+domain, redis.Z{Score: score, Member: id}).Err(); err != nil {
			t.Fatal(err)
		}
		if err := f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: score, Member: domain}).Err(); err != nil {
			t.Fatal(err)
		}
	}
	p, err := f.authority.StageGreenhouseOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID, second})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", p.digest); err != nil {
			t.Error("two-provider fixture plan retirement failed")
		}
	})
	activateFixturePlan(t, f, p)
	if err := f.client.redis.Set(ctx, ownershipProjectionKey, p.projection, 0).Err(); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, p.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	if err := f.client.redis.Set(ctx, "ratelimit:greenhouse", seconds(time.Now().Add(time.Hour)), time.Hour).Err(); err != nil {
		t.Fatal(err)
	}
	claim, err := a.Claim(ctx, Simple)
	if err != nil || claim == nil || claim.Descriptor().ID != second {
		t.Fatalf("older throttled provider hid available Ashby work: claim=%v err=%v", claim, err)
	}
	if score, err := f.client.redis.ZScore(ctx, "monitors_simple:greenhouse", f.task.ID).Result(); err != nil || score != 1 {
		t.Fatal("rate-limited provider's deadline changed")
	}
	if f.client.redis.ZCard(ctx, "inflight:simple").Val() != 1 || f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 1 {
		t.Fatal("cross-provider claim lost exact lease conservation")
	}
}

func TestRealOwnedNativeLegacyClaimRace(t *testing.T) {
	f, a, p := realOwnedAuthority(t)
	ctx := context.Background()
	foreign := "00000000-0000-4000-8000-000000000099"
	addQueuedMonitor(t, f.client, foreign, "greenhouse", 1, false)
	var native *Claim
	var legacy *Task
	var nativeErr, legacyErr error
	var wait sync.WaitGroup
	wait.Add(2)
	go func() { defer wait.Done(); native, nativeErr = a.Claim(ctx, Simple) }()
	go func() {
		defer wait.Done()
		legacyErr = f.authority.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
			if _, err := f.authority.loadActiveOwnership(ctx, tx, p.digest, p.SourceRevision()); err != nil {
				return err
			}
			var err error
			legacy, err = f.client.ClaimLegacyBound(ctx, Simple, p)
			return err
		})
	}()
	wait.Wait()
	if nativeErr != nil || legacyErr != nil || native == nil || legacy == nil || native.Descriptor().ID != f.task.ID || legacy.ID != foreign {
		t.Fatalf("owners collided or stranded: native=%v legacy=%v", nativeErr, legacyErr)
	}
	if f.client.redis.ZCard(ctx, "inflight:simple").Val() != 2 {
		t.Fatal("claim conservation mismatch")
	}
}

func TestRealOwnedRevocationBeforeCanonicalEffects(t *testing.T) {
	for _, mode := range []string{"disabled", "canonical_config", "projection_loss", "retired_plan", "retired_epoch"} {
		t.Run(mode, func(t *testing.T) {
			f, a, p := realOwnedAuthority(t)
			ctx := context.Background()
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("owned claim failed")
			}
			switch mode {
			case "disabled":
				_, err = f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID)
			case "canonical_config":
				_, err = f.observer.Exec(ctx, `UPDATE job_board SET metadata='{"token":"changed","scraper_type":"skip"}'::jsonb WHERE id=$1::uuid`, f.task.ID)
			case "projection_loss":
				err = f.client.redis.Del(ctx, ownershipProjectionKey).Err()
			case "retired_plan":
				_, err = f.observer.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1", p.digest)
			case "retired_epoch":
				_, err = f.observer.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
			}
			if err != nil {
				t.Fatal(err)
			}
			before := snapshot(t, f.client)
			called := false
			if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); err == nil || called {
				t.Fatal("revoked owner entered callback")
			}
			if err := a.Heartbeat(ctx, claim); err == nil {
				t.Fatal("revoked owner extended lease")
			}
			if !reflect.DeepEqual(before, snapshot(t, f.client)) {
				t.Fatal("revoked operations changed Redis")
			}
			assertCanonical(t, f, false)
		})
	}
}

func TestRealOwnedOpenRejectsMissingProjectionAndWrongIdentity(t *testing.T) {
	f, a, p := realOwnedAuthority(t)
	a.Close()
	ctx := context.Background()
	for _, revision := range []string{strings.Repeat("b", 40), p.SourceRevision()} {
		if revision == p.SourceRevision() {
			if err := f.client.redis.Del(ctx, ownershipProjectionKey).Err(); err != nil {
				t.Fatal(err)
			}
		}
		if owner, err := OpenOwnedAuthority(ctx, f.dsn, f.client, f.epoch, p.digest, revision); owner != nil || !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("startup adopted missing or stale ownership")
		}
	}
}

func TestOwnedProbeDistinguishesMissingAndZero(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	if err := c.redis.ZAdd(ctx, "monitors_simple:greenhouse", redis.Z{Score: 0, Member: profileBoardID}).Err(); err != nil {
		t.Fatal(err)
	}
	pipe := c.redis.Pipeline()
	missing := pipe.Do(ctx, "ZMSCORE", "ft_monitors_simple:greenhouse", profileBoardID)
	due := pipe.Do(ctx, "ZMSCORE", "monitors_simple:greenhouse", profileBoardID)
	if _, err := pipe.Exec(ctx); err != nil {
		t.Fatal("nullable probe masked a later score")
	}
	if value, err := ownershipProbeScore(missing); err != nil || value != nil {
		t.Fatal("missing score became due work")
	}
	if value, err := ownershipProbeScore(due); err != nil || value == nil || *value != 0 {
		t.Fatal("zero score was lost after a missing representation")
	}
}

func TestInstalledOwnershipPreservesB0AndInflightDuplicateRepair(t *testing.T) {
	for _, guarded := range []bool{false, true} {
		t.Run(fmt.Sprint(guarded), func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			p := privateOwnershipProjection(t, c, testOwnershipDocument(t))
			id := "00000000-0000-4000-8000-000000000099"
			member := "scrape|greenhouse|" + id
			if err := c.redis.ZAdd(ctx, "scrapes_simple:greenhouse", redis.Z{Score: 1, Member: id}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := c.redis.ZAdd(ctx, "ready:simple:2", redis.Z{Score: 1, Member: "greenhouse"}).Err(); err != nil {
				t.Fatal(err)
			}
			if guarded {
				if err := c.redis.HSet(ctx, "lightpanda-b0:legacy-guard", id, "owned").Err(); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := c.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 12345678999, Member: member}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := c.redis.HSet(ctx, "inflight_tokens:simple", member, strings.Repeat("a", 32)).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if task, err := c.ClaimLegacyBound(ctx, Simple, p); err != nil || task != nil {
				t.Fatalf("duplicate obtained new authority: %v", err)
			}
			if c.redis.ZCard(ctx, "scrapes_simple:greenhouse").Val() != 0 || c.redis.ZCard(ctx, "ready:simple:2").Val() != 0 {
				t.Fatal("protected duplicate stranded global priority")
			}
			if guarded {
				if c.redis.HGet(ctx, "lightpanda-b0:legacy-guard", id).Val() != "owned" {
					t.Fatal("B0 guard changed")
				}
			} else {
				if c.redis.ZScore(ctx, "inflight:simple", member).Val() != 12345678999 || c.redis.HGet(ctx, "inflight_tokens:simple", member).Val() != strings.Repeat("a", 32) {
					t.Fatal("duplicate repair replaced live native attempt")
				}
			}
		})
	}
}

func TestInstalledOwnershipLegacyExcludesMemberAfterBrowserRouteDrift(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	p := privateOwnershipProjection(t, c, testOwnershipDocument(t))
	if err := c.redis.ZAdd(ctx, "monitors_browser:changed", redis.Z{Score: 1, Member: profileBoardID}, redis.Z{Score: 2, Member: "foreign"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.ZAdd(ctx, "ready:browser:1", redis.Z{Score: 1, Member: "changed"}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := c.redis.HSet(ctx, "board:foreign", "domain", "changed").Err(); err != nil {
		t.Fatal(err)
	}
	task, err := c.ClaimLegacyBound(ctx, Browser, p)
	if err != nil || task == nil || task.ID != "foreign" {
		t.Fatal("legacy browser route adopted selected member")
	}
	if score, err := c.redis.ZScore(ctx, "monitors_browser:changed", profileBoardID).Result(); err != nil || score != 1 {
		t.Fatal("drifted selected member lost source score")
	}
}

func TestRealOwnedDelayedDisableRejectsBeforePop(t *testing.T) {
	f, a, _ := realOwnedAuthority(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	blocker, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	var pid int
	if err := blocker.QueryRow(ctx, "SELECT pg_backend_pid()").Scan(&pid); err != nil {
		t.Fatal(err)
	}
	if _, err := blocker.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() {
		claim, err := a.Claim(ctx, Simple)
		if claim != nil {
			done <- ErrProtocol
			return
		}
		done <- err
	}()
	for {
		select {
		case err := <-done:
			t.Fatalf("claim did not wait for canonical update: %v", err)
		default:
		}
		var waiting bool
		if err := f.observer.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name='jobseek:crawler:ordinary-authority:local' AND $1::integer=ANY(pg_blocking_pids(pid)))", pid).Scan(&waiting); err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("native claim never reached canonical row barrier")
		}
		time.Sleep(5 * time.Millisecond)
	}
	before := snapshot(t, f.client)
	if err := blocker.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	if err := awaitWrite(t, done); !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatalf("delayed disable still popped work: %v", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("disabled native claim changed Redis")
	}
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&count); err != nil || count != 0 {
		t.Fatal("disabled claim activated database fence")
	}
}

func TestRealOwnershipFleetClaimProjection(t *testing.T) {
	c := privateRedis(t)
	ctx := context.Background()
	doc := testOwnershipDocument(t)
	doc.Members = nil
	for i := 1; i <= 4297; i++ {
		id := fmt.Sprintf("00000000-0000-4000-8000-%012d", i)
		config := profileConfig()
		profile, err := InspectRichMonitor(id, config)
		if err != nil {
			t.Fatal(err)
		}
		doc.Members = append(doc.Members, ownershipMember{id, profile.CompanyID, profile.Domain, Monitor, Simple, profile.Profile, profile.EffectiveConfigSHA256, config})
	}
	plan := privateOwnershipProjection(t, c, doc)
	if len(plan.projection)*3 >= len(plan.body) {
		t.Fatal("fleet projection did not remove configuration decode cost")
	}
	last := doc.Members[len(doc.Members)-1]
	addQueuedMonitor(t, c, last.BoardID, last.Domain, 1, false)
	if err := c.redis.HSet(ctx, "board:"+last.BoardID, last.Config).Err(); err != nil {
		t.Fatal(err)
	}
	before := snapshot(t, c)
	started := time.Now()
	for i := 0; i < 16; i++ {
		task, err := c.ClaimLegacyBound(ctx, Simple, plan)
		if err != nil || task != nil {
			t.Fatal("fleet legacy claim did not preserve native membership", err)
		}
	}
	t.Logf("4297-board legacy claims: full payload %d bytes, projection %d bytes, mean %.3f ms", len(plan.body), len(plan.projection), float64(time.Since(started).Microseconds())/16000)
	after := snapshot(t, c)
	for key, value := range before {
		if !strings.HasPrefix(key, "ordinary:claim-cursor:") && after[key] != value {
			t.Fatal("legacy claim changed retained queue/configuration state")
		}
	}
	task, err := nativeQueueClaim(t, c, plan, last.BoardID, last.Config)
	if err != nil || task == nil || task.ID != last.BoardID {
		t.Fatal("native could not claim final fleet member", err)
	}
}

func TestCompactOwnershipRejectsMalformedBoundProjectionBeforeEffects(t *testing.T) {
	for _, mode := range []string{"plan", "epoch", "source", "version", "empty", "member", "domain", "array"} {
		t.Run(mode, func(t *testing.T) {
			c := privateRedis(t)
			ctx := context.Background()
			doc := testOwnershipDocument(t)
			plan := privateOwnershipProjection(t, c, doc)
			member := doc.Members[0]
			addQueuedMonitor(t, c, member.BoardID, member.Domain, 1, false)
			var projected map[string]any
			if json.Unmarshal([]byte(plan.projection), &projected) != nil {
				t.Fatal("invalid fixture")
			}
			switch mode {
			case "plan":
				projected["plan_sha256"] = strings.Repeat("b", 64)
			case "epoch":
				projected["routing_epoch"] = 8
			case "source":
				projected["source_revision"] = strings.Repeat("b", 40)
			case "version":
				projected["version"] = "unknown"
			case "empty":
				projected["members"] = map[string]string{}
			case "member":
				projected["members"] = map[string]string{"invalid": "greenhouse"}
			case "domain":
				projected["members"] = map[string]any{member.BoardID: 7}
			case "array":
				projected["members"] = []string{member.BoardID}
			}
			body, err := json.Marshal(projected)
			if err != nil {
				t.Fatal(err)
			}
			if err := c.redis.Set(ctx, ownershipProjectionKey, body, 0).Err(); err != nil {
				t.Fatal(err)
			}
			// A private low-level caller supplying this malformed document's matching
			// byte hash still cannot pop, throttle, mutate a cursor or take a lease.
			hash := sha1.Sum(body)
			copy := *plan
			copy.projectionHash = hex.EncodeToString(hash[:])
			before := snapshot(t, c)
			if task, err := c.ClaimLegacyBound(ctx, Simple, &copy); err == nil || task != nil {
				t.Fatal("malformed routing document accepted")
			}
			if !reflect.DeepEqual(before, snapshot(t, c)) {
				t.Fatal("malformed routing document changed queue state")
			}
		})
	}
}
