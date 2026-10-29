package queue

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealNativePrioritySkipsUnclaimableClassLocks(t *testing.T) {
	for _, mode := range []string{"monitor-turn", "detail-turn", "details-only-during-monitor-turn", "first-time-turn"} {
		t.Run(mode, func(t *testing.T) {
			p := firstJSONLDDetailFixture(t)
			ctx := context.Background()
			monitor := p.plan.document.Members[0]
			detail := p.plan.document.Details[0]
			if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET next_check_at=now()-interval '1 minute' WHERE id=$1::uuid", monitor.BoardID); err != nil {
				t.Fatal("private monitor deadline required")
			}
			if err := p.f.client.redis.ZAdd(ctx, "monitors_simple:"+monitor.Domain, redis.Z{Score: 1, Member: monitor.BoardID}).Err(); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.ZAdd(ctx, "ready:simple:1", redis.Z{Score: 1, Member: monitor.Domain}).Err(); err != nil {
				t.Fatal(err)
			}
			if mode != "first-time-turn" {
				if err := p.f.client.redis.Del(ctx, "ft_scrapes_simple:"+p.f.task.Domain, "ready:simple:0").Err(); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.ZAdd(ctx, "scrapes_simple:"+p.f.task.Domain, redis.Z{Score: 1, Member: p.f.task.ID}).Err(); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.ZAdd(ctx, "ready:simple:2", redis.Z{Score: 1, Member: p.f.task.Domain}).Err(); err != nil {
					t.Fatal(err)
				}
			}
			streak := 7
			if mode == "detail-turn" || mode == "first-time-turn" {
				streak = 8
			}
			if err := p.f.client.redis.Set(ctx, "claim:recurring-monitor-streak:simple", streak, 0).Err(); err != nil {
				t.Fatal(err)
			}
			if _, err := applyFirstFixture(t, p, false); err != nil {
				t.Fatal("private activation failed")
			}
			a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
			if err != nil {
				t.Fatal("private native authority unavailable")
			}
			defer a.Close()
			blockedBoard := detail.BoardID
			if mode == "detail-turn" || mode == "first-time-turn" {
				blockedBoard = monitor.BoardID
			}
			lock, err := p.f.observer.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer lock.Rollback(ctx)
			if _, err = lock.Exec(ctx, "SELECT id FROM job_board WHERE id=$1::uuid FOR UPDATE", blockedBoard); err != nil {
				t.Fatal(err)
			}
			bounded, cancel := context.WithTimeout(ctx, time.Second)
			started := time.Now()
			var claim *Claim
			if mode == "details-only-during-monitor-turn" {
				claim, err = a.ClaimDetails(bounded, Simple)
			} else {
				claim, err = a.Claim(bounded, Simple)
			}
			cancel()
			elapsed := time.Since(started)
			if e := lock.Rollback(ctx); e != nil {
				t.Fatal("private row lock release failed")
			}
			if err != nil {
				t.Fatalf("unclaimable class blocked current priority (elapsed_ms=%d)", elapsed.Milliseconds())
			}
			if mode == "details-only-during-monitor-turn" {
				if claim != nil {
					t.Fatal("detail-only claimant crossed monitor priority")
				}
				if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
					t.Fatal("idle hint acquired a lease")
				}
			} else {
				want := Monitor
				if mode == "detail-turn" || mode == "first-time-turn" {
					want = Scrape
				}
				if claim == nil || claim.task.Kind != want {
					t.Fatal("wrong task class claimed")
				}
			}
			next, err := a.Claim(ctx, Simple)
			wantNext := Scrape
			if mode != "monitor-turn" {
				wantNext = Monitor
			}
			if err != nil || next == nil || next.task.Kind != wantNext {
				t.Fatal("deferred class did not remain claimable after the priority turn")
			}
			t.Logf("current-priority observation completed in %d microseconds", elapsed.Microseconds())
		})
	}
}

func TestRealNativePriorityHintRetainsAtomicFirstTimeDecision(t *testing.T) {
	p := firstOwnershipFixture(t)
	ctx := context.Background()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	if priority, err := p.f.client.readyPriority(ctx, Simple); err != nil || priority != 1 {
		t.Fatal("initial monitor hint unavailable")
	}
	id := ordinaryID(t)
	domain := "priority-fixture.invalid"
	if err := p.f.client.redis.HSet(ctx, "scrape:"+id, map[string]string{"board_id": id, "domain": domain}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZAdd(ctx, "ft_scrapes_simple:"+domain, redis.Z{Score: 1, Member: id}).Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.ZAdd(ctx, "ready:simple:0", redis.Z{Score: 1, Member: domain}).Err(); err != nil {
		t.Fatal(err)
	}
	member := p.plan.document.Members[0]
	current, err := p.f.client.redis.HGetAll(ctx, "board:"+member.BoardID).Result()
	if err != nil {
		t.Fatal(err)
	}
	body, err := json.Marshal(current)
	if err != nil {
		t.Fatal(err)
	}
	task, err := p.f.client.claimTaskBound(ctx, Simple, strings.Repeat("a", 32), p.plan.claimBinding("native", member.BoardID, string(body)))
	if err != nil || task != nil {
		t.Fatal("stale monitor hint bypassed the original atomic first-time rule")
	}
	if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || p.f.client.redis.ZCard(ctx, "ft_scrapes_simple:"+domain).Val() != 1 {
		t.Fatal("rejected stale hint changed the pending work or acquired a lease")
	}
}

func TestRealNativePriorityObservationRejectsMalformedSharedState(t *testing.T) {
	p := firstOwnershipFixture(t)
	ctx := context.Background()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal(err)
	}
	a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal(err)
	}
	defer a.Close()
	for _, value := range []string{"invalid", "-1", "1.5", "NaN", "Inf"} {
		if err := p.f.client.redis.Set(ctx, "claim:recurring-monitor-streak:simple", value, 0).Err(); err != nil {
			t.Fatal(err)
		}
		if task, err := a.Claim(ctx, Simple); !errors.Is(err, ErrObservation) || task != nil {
			t.Fatal("malformed fairness state was treated as a claim or idle result")
		}
	}
	if err := p.f.client.redis.Del(ctx, "claim:recurring-monitor-streak:simple").Err(); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.Set(ctx, "ready:simple:0", "wrong type", 0).Err(); err != nil {
		t.Fatal(err)
	}
	if task, err := a.Claim(ctx, Simple); !errors.Is(err, ErrObservation) || task != nil {
		t.Fatal("malformed ready state was treated as a claim or idle result")
	}
	if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("failed priority observation acquired a lease")
	}
}
