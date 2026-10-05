package queue

import (
	"context"
	"errors"
	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
	"testing"
	"time"
)

func firstRenderedMonitorFixture(t *testing.T) firstOwnerFixture {
	return firstRenderedMonitorProviderFixture(t, "dom")
}

func firstRenderedMonitorProviderFixture(t *testing.T, provider string) firstOwnerFixture {
	p := firstOwnershipFixture(t)
	ctx := context.Background()
	id := p.f.task.ID

	md := `{"render":true,"scraper_type":"json-ld"}`
	if provider == "nextdata" {
		md = `{"render":true,"path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"scraper_type":"json-ld"}`
	}
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type=$3,monitor_needs_browser=true,metadata=$2::jsonb WHERE id=$1::uuid", id, md, provider); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+id, "crawler_type", provider, "monitor_needs_browser", "1", "metadata", md).Err(); e != nil {
		t.Fatal(e)
	}
	p.f.client.redis.Del(ctx, "monitors_simple:"+p.f.task.Domain, "ft_monitors_simple:"+p.f.task.Domain, "ready:simple:1")
	p.f.client.redis.ZAdd(ctx, "monitors_browser:"+p.f.task.Domain, redis.Z{Score: 1, Member: id})
	p.f.client.redis.ZAdd(ctx, "ready:browser:1", redis.Z{Score: 1, Member: p.f.task.Domain})
	p.f.task.Worker = Browser
	var e error
	p.plan, e = p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{id}, nil)
	if e != nil {
		t.Fatal(e)
	}
	return p
}

func TestRealRenderedMonitorColdRetirementRestoresBrowserDeadline(t *testing.T) {
	for _, mode := range []string{"active", "claim-before-sql", "disabled"} {
		t.Run(mode, func(t *testing.T) {
			p := firstRenderedMonitorFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			if mode == "claim-before-sql" {
				if _, e := p.f.observer.Exec(ctx, "DELETE FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", p.f.task.ID); e != nil {
					t.Fatal(e)
				}
			}
			if mode == "disabled" {
				if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.f.task.ID); e != nil {
					t.Fatal(e)
				}
			}
			due := firstRetirementDue(t, p)
			canonical := coldCanonicalSnapshot(t, p.f)
			if _, e := applyFirstFixture(t, p, true); e != nil {
				t.Fatal(e)
			}
			if canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("reversal changed canonical receipt")
			}
			task := inflight(p.f.task)
			if p.f.client.redis.ZScore(ctx, "inflight:browser", task).Err() != redis.Nil || p.f.client.redis.HExists(ctx, "inflight_tokens:browser", task).Val() {
				t.Fatal("native lease retained")
			}
			score, e := p.f.client.redis.ZScore(ctx, "monitors_browser:"+p.f.task.Domain, p.f.task.ID).Result()
			if due == nil {
				if e != redis.Nil {
					t.Fatal("disabled board requeued")
				}
			} else if e != nil || score != seconds(*due) {
				t.Fatal("browser canonical deadline lost", e)
			}
			ready, e := p.f.client.redis.ZScore(ctx, "ready:browser:1", p.f.task.Domain).Result()
			if due == nil {
				if e != redis.Nil {
					t.Fatal("disabled browser domain remained ready")
				}
			} else {
				rate, rateErr := p.f.client.redis.Get(ctx, "ratelimit:"+p.f.task.Domain).Float64()
				if rateErr != nil && rateErr != redis.Nil {
					t.Fatal(rateErr)
				}
				if e != nil || ready != max(seconds(*due), rate) {
					t.Fatal("browser reversal lost ready-domain routing", e)
				}
			}
			if p.f.client.redis.ZScore(ctx, "ready:simple:1", p.f.task.Domain).Err() != redis.Nil {
				t.Fatal("browser reversal published a simple ready domain")
			}
			if !errors.Is(a.Heartbeat(ctx, claim), ErrAuthorityLost) {
				t.Fatal("retired browser retained authority")
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			if p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("projection retained after restart")
			}
		})
	}
}

func TestRealRenderedMonitorRetiredAttemptAllowsLaterReversal(t *testing.T) {
	old := firstRenderedMonitorFixture(t)
	firstRetirementClaim(t, old)
	if _, e := applyFirstFixture(t, old, true); e != nil {
		t.Fatal(e)
	}
	current := firstOwnershipFixtureHistory(t, true)
	firstRetirementClaim(t, current)
	if _, e := applyFirstFixture(t, current, true); e != nil {
		t.Fatal("old Browser monitor blocked later reversal", e)
	}
}

func TestRealRenderedNextdataRetiredAttemptAllowsLaterReversal(t *testing.T) {
	old := firstRenderedMonitorProviderFixture(t, "nextdata")
	firstRetirementClaim(t, old)
	if _, err := applyFirstFixture(t, old, true); err != nil {
		t.Fatal(err)
	}
	current := firstOwnershipFixtureHistory(t, true)
	firstRetirementClaim(t, current)
	if _, err := applyFirstFixture(t, current, true); err != nil {
		t.Fatal("old NextData browser receipt blocked later reversal", err)
	}
}

func TestRealRenderedMonitorActivationRefusesEitherNamespaceLease(t *testing.T) {
	for _, worker := range []WorkerType{Simple, Browser} {
		t.Run(string(worker), func(t *testing.T) {
			p := firstRenderedMonitorFixture(t)
			ctx := context.Background()
			p.f.client.redis.HSet(ctx, "inflight_tokens:"+string(worker), inflight(p.f.task), "foreign")
			if _, e := applyFirstFixture(t, p, false); e == nil {
				t.Fatal("foreign monitor lease adopted")
			}
			if p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("refusal published owner")
			}
		})
	}
}

func TestRealRenderedMonitorCommittedReapingRestoresFuture(t *testing.T) {
	p := firstRenderedMonitorFixture(t)
	a, claim := firstRetirementClaim(t, p)
	ctx := context.Background()
	due := time.Now().UTC().Add(time.Hour).Truncate(time.Microsecond)
	host := "jobs.example.net"
	learned := &host
	if _, e := a.write(ctx, claim, true, &learned, func(ctx context.Context, tx pgx.Tx) error {
		_, e := tx.Exec(ctx, "UPDATE job_board SET next_check_at=$2 WHERE id=$1::uuid", p.f.task.ID, due)
		return e
	}); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.ZAdd(ctx, "inflight:browser", redis.Z{Score: 1, Member: inflight(p.f.task)}).Err(); e != nil {
		t.Fatal(e)
	}
	if _, e := reapCommittedFixture(t, p); e != nil {
		t.Fatal(e)
	}
	score, e := p.f.client.redis.ZScore(ctx, "monitors_browser:"+p.f.task.Domain, p.f.task.ID).Result()
	if e != nil || score != seconds(due) {
		t.Fatal("committed Browser deadline lost", e)
	}
	if p.f.client.redis.HGet(ctx, "board:"+p.f.task.ID, "egress_host").Val() != host || p.f.client.redis.HExists(ctx, "inflight_tokens:browser", inflight(p.f.task)).Val() {
		t.Fatal("reaping lost host or retained native token")
	}
	if _, e := applyFirstFixture(t, p, true); e != nil {
		t.Fatal(e)
	}
}

func TestRealRenderedNextdataColdRetirementRestoresBrowserDeadline(t *testing.T) {
	for _, mode := range []string{"active", "claim-before-sql", "disabled"} {
		t.Run(mode, func(t *testing.T) {
			p := firstRenderedMonitorProviderFixture(t, "nextdata")
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			if mode == "claim-before-sql" {
				if _, e := p.f.observer.Exec(ctx, "DELETE FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", p.f.task.ID); e != nil {
					t.Fatal(e)
				}
			}
			if mode == "disabled" {
				if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", p.f.task.ID); e != nil {
					t.Fatal(e)
				}
			}
			due := firstRetirementDue(t, p)
			canonical := coldCanonicalSnapshot(t, p.f)
			if _, e := applyFirstFixture(t, p, true); e != nil {
				t.Fatal(e)
			}
			if canonical != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("reversal changed canonical receipt")
			}
			task := inflight(p.f.task)
			if p.f.client.redis.ZScore(ctx, "inflight:browser", task).Err() != redis.Nil || p.f.client.redis.HExists(ctx, "inflight_tokens:browser", task).Val() {
				t.Fatal("native lease retained")
			}
			score, e := p.f.client.redis.ZScore(ctx, "monitors_browser:"+p.f.task.Domain, p.f.task.ID).Result()
			if due == nil {
				if e != redis.Nil {
					t.Fatal("disabled board requeued")
				}
			} else if e != nil || score != seconds(*due) {
				t.Fatal("browser canonical deadline lost", e)
			}
			ready, e := p.f.client.redis.ZScore(ctx, "ready:browser:1", p.f.task.Domain).Result()
			if due == nil {
				if e != redis.Nil {
					t.Fatal("disabled browser domain remained ready")
				}
			} else {
				rate, rateErr := p.f.client.redis.Get(ctx, "ratelimit:"+p.f.task.Domain).Float64()
				if rateErr != nil && rateErr != redis.Nil {
					t.Fatal(rateErr)
				}
				if e != nil || ready != max(seconds(*due), rate) {
					t.Fatal("browser reversal lost ready-domain routing", e)
				}
			}
			if p.f.client.redis.ZScore(ctx, "ready:simple:1", p.f.task.Domain).Err() != redis.Nil {
				t.Fatal("browser reversal published a simple ready domain")
			}
			if !errors.Is(a.Heartbeat(ctx, claim), ErrAuthorityLost) {
				t.Fatal("retired browser retained authority")
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			if p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("projection retained after restart")
			}
		})
	}
}
