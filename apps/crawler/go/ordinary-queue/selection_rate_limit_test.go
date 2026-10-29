package queue

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestRealNativeCandidateRateObservationRetainsFailureGuards(t *testing.T) {
	for _, detail := range []bool{false, true} {
		name := "monitor"
		if detail {
			name = "detail"
		}
		t.Run(name, func(t *testing.T) {
			var p firstOwnerFixture
			if detail {
				p = firstJSONLDDetailFixture(t)
			} else {
				p = firstOwnershipFixture(t)
			}
			ctx := context.Background()
			if _, err := applyFirstFixture(t, p, false); err != nil {
				t.Fatal("private activation failed")
			}
			a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
			if err != nil {
				t.Fatal("private native authority unavailable")
			}
			defer a.Close()
			key := "ratelimit:" + p.f.task.Domain
			for _, value := range []string{"not-a-deadline", "NaN", "+Inf", ""} {
				if err := p.f.client.redis.Set(ctx, key, value, time.Minute).Err(); err != nil {
					t.Fatal("private invalid delay unavailable")
				}
				if claim, err := a.Claim(ctx, Simple); !errors.Is(err, ErrObservation) || claim != nil {
					t.Fatal("invalid shared delay was treated as eligible or idle")
				}
			}
			if err := p.f.client.redis.Del(ctx, key).Err(); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.HSet(ctx, key, "wrong", "type").Err(); err != nil {
				t.Fatal(err)
			}
			if claim, err := a.Claim(ctx, Simple); !errors.Is(err, ErrObservation) || claim != nil {
				t.Fatal("wrong-type shared delay was treated as eligible or idle")
			}
			if err := p.f.client.redis.Del(ctx, key).Err(); err != nil {
				t.Fatal(err)
			}
			now, err := p.f.client.clock(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.Set(ctx, key, number(now+120), 2*time.Minute).Err(); err != nil {
				t.Fatal(err)
			}
			if err := p.f.client.redis.Set(ctx, ownershipProjectionKey, p.plan.projection+" ", 0).Err(); err != nil {
				t.Fatal(err)
			}
			if claim, err := a.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) || claim != nil {
				t.Fatal("future delay hid changed ownership authority")
			}
			if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("failed observation acquired a lease")
			}
		})
	}
}

func TestRealNativeCandidateSelectionSkipsFutureRateLimitedMonitor(t *testing.T) {
	testNativeFutureRateCandidate(t, false)
}
func TestRealNativeCandidateSelectionSkipsFutureRateLimitedDetail(t *testing.T) {
	testNativeFutureRateCandidate(t, true)
}
func testNativeFutureRateCandidate(t *testing.T, detail bool) {
	var p firstOwnerFixture
	if detail {
		p = firstJSONLDDetailFixture(t)
	} else {
		p = firstOwnershipFixture(t)
	}
	ctx := context.Background()
	if _, err := applyFirstFixture(t, p, false); err != nil {
		t.Fatal("private activation failed")
	}
	a, err := OpenOwnedAuthority(ctx, p.f.dsn, p.f.client, p.f.epoch, p.plan.digest, p.plan.SourceRevision())
	if err != nil {
		t.Fatal("private native authority unavailable")
	}
	defer a.Close()
	board, domain, queueKey := p.f.task.ID, p.f.task.Domain, "monitors_simple:"+p.f.task.Domain
	if detail {
		board = p.plan.document.Details[0].BoardID
		queueKey = "ft_scrapes_simple:" + domain
	}
	before, err := p.f.client.redis.ZScore(ctx, queueKey, p.f.task.ID).Result()
	if err != nil {
		t.Fatal("original due task required")
	}
	now, err := p.f.client.clock(ctx)
	if err != nil {
		t.Fatal("private Redis clock unavailable")
	}
	if err = p.f.client.redis.Set(ctx, "ratelimit:"+domain, number(now+120), 2*time.Minute).Err(); err != nil {
		t.Fatal("private publisher delay unavailable")
	}
	lock, err := p.f.observer.Begin(ctx)
	if err != nil {
		t.Fatal("private observation lock unavailable")
	}
	defer lock.Rollback(ctx)
	if _, err = lock.Exec(ctx, "SELECT id FROM job_board WHERE id=$1::uuid FOR UPDATE", board); err != nil {
		t.Fatal("private board lock unavailable")
	}
	bounded, cancel := context.WithTimeout(ctx, time.Second)
	started := time.Now()
	claim, claimErr := a.Claim(bounded, Simple)
	cancel()
	elapsed := time.Since(started)
	if err = lock.Rollback(ctx); err != nil {
		t.Fatal("private row lock release failed")
	}
	if claimErr != nil || claim != nil {
		t.Fatalf("future-limited task reached canonical candidate inspection (elapsed_ms=%d error_type=%T observation=%t authority=%t configuration=%t claimed=%t)", elapsed.Milliseconds(), claimErr, errors.Is(claimErr, ErrObservation), errors.Is(claimErr, ErrAuthorityLost), errors.Is(claimErr, ErrConfiguration), claim != nil)
	}
	after, err := p.f.client.redis.ZScore(ctx, queueKey, p.f.task.ID).Result()
	if err != nil || before != after {
		t.Fatal("observation changed original task deadline")
	}
	if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("future-limited observation acquired a lease")
	}
	if err = p.f.client.redis.Del(ctx, "ratelimit:"+domain).Err(); err != nil {
		t.Fatal("private delay release failed")
	}
	bounded, cancel = context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	claim, err = a.Claim(bounded, Simple)
	if err != nil || claim == nil || claim.task.ID != p.f.task.ID {
		t.Fatalf("released publisher delay stranded the original task (error_type=%T observation=%t authority=%t configuration=%t claimed=%t)", err, errors.Is(err, ErrObservation), errors.Is(err, ErrAuthorityLost), errors.Is(err, ErrConfiguration), claim != nil)
	}
	t.Logf("future-limited candidate observation completed in %d microseconds", elapsed.Microseconds())
}
