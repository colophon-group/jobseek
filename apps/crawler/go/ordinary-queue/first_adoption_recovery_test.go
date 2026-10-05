package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

func TestRealFirstAdoptionRestoresExpiredLegacyCanonicalDeadlines(t *testing.T) {
	for name, fixture := range map[string]func(*testing.T) firstOwnerFixture{
		"simple-monitor":  firstOwnershipFixture,
		"browser-monitor": firstRenderedMonitorFixture,
		"simple-detail":   firstWorkdayDetailFixture,
		"browser-detail":  firstRenderedDetailFixture,
	} {
		for _, mode := range []string{"tokenless", "tokenized", "save-failure"} {
			t.Run(name+"/"+mode, func(t *testing.T) {
				p := fixture(t)
				ctx := context.Background()
				task := inflight(p.f.task)
				worker := string(p.f.task.Worker)
				if err := p.f.client.redis.ZAdd(ctx, "inflight:"+worker, redis.Z{Score: 1, Member: task}).Err(); err != nil {
					t.Fatal(err)
				}
				if mode == "tokenized" {
					if err := p.f.client.redis.HSet(ctx, "inflight_tokens:"+worker, task, strings.Repeat("f", 32)).Err(); err != nil {
						t.Fatal(err)
					}
				}
				if err := p.f.client.redis.HSet(ctx, "inflight_strikes:"+worker, task, "2").Err(); err != nil {
					t.Fatal(err)
				}
				var due *time.Time
				query := "SELECT next_check_at FROM job_board WHERE id=$1::uuid"
				prefix := "monitors_"
				if p.f.task.Kind == Scrape {
					query = "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid"
					prefix = "scrapes_"
				}
				if err := p.f.observer.QueryRow(ctx, query, p.f.task.ID).Scan(&due); err != nil || due == nil {
					t.Fatal("canonical deadline missing", err)
				}
				canonical, before := coldCanonicalSnapshot(t, p.f), snapshot(t, p.f.client)
				if mode == "save-failure" {
					firstFixtureSave(t, p, false)
					if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrObservation) || firstFixtureState(t, p) != "staged" {
						t.Fatal("unacknowledged adoption granted SQL ownership", err)
					}
					firstFixtureSave(t, p, true)
				}
				result, err := applyFirstFixture(t, p, false)
				if err != nil || result.State != "active" {
					t.Fatal("expired legacy adoption failed", err)
				}
				if canonical != coldCanonicalSnapshot(t, p.f) {
					t.Fatal("adoption rewrote canonical content, leases or attempts")
				}
				if p.f.client.redis.ZScore(ctx, "inflight:"+worker, task).Err() != redis.Nil || p.f.client.redis.HExists(ctx, "inflight_tokens:"+worker, task).Val() {
					t.Fatal("expired claim retained")
				}
				if p.f.client.redis.HGet(ctx, "inflight_strikes:"+worker, task).Val() != "2" {
					t.Fatal("adoption invented successful fetch")
				}
				score, err := p.f.client.redis.ZScore(ctx, prefix+worker+":"+p.f.task.Domain, p.f.task.ID).Result()
				if err != nil || score != seconds(*due) {
					t.Fatal("canonical deadline lost", err, score, seconds(*due))
				}
				after := snapshot(t, p.f.client)
				for key, value := range before {
					if strings.HasPrefix(key, "lightpanda-b0:") && !reflect.DeepEqual(value, after[key]) {
						t.Fatal("adoption changed B0", key)
					}
				}
				restartPublicationRedisWithoutSave(t, p.f.client)
				if p.f.client.redis.ZScore(ctx, "inflight:"+worker, task).Err() != redis.Nil {
					t.Fatal("expired claim survived persisted adoption")
				}
				if _, err := applyFirstFixture(t, p, true); err != nil {
					t.Fatal("adopted lane cannot cold reverse", err)
				}
			})
		}
	}
}

func TestRealFirstAdoptionPreflightsLegacyRecoveryBeforeEffects(t *testing.T) {
	for _, mode := range []string{"live-redis", "wrong-worker", "invalid-token", "orphan-token", "invalid-queue", "live-sql"} {
		t.Run(mode, func(t *testing.T) {
			p := firstOwnershipFixture(t)
			ctx := context.Background()
			task := inflight(p.f.task)
			worker, score := "simple", float64(1)
			if mode == "wrong-worker" {
				worker = "browser"
			}
			if mode == "live-redis" {
				score = seconds(time.Now().Add(time.Hour))
			}
			if err := p.f.client.redis.ZAdd(ctx, "inflight:"+worker, redis.Z{Score: score, Member: task}).Err(); err != nil {
				t.Fatal(err)
			}
			switch mode {
			case "invalid-token":
				p.f.client.redis.HSet(ctx, "inflight_tokens:simple", task, "corrupt")
			case "orphan-token":
				p.f.client.redis.ZRem(ctx, "inflight:simple", task)
				p.f.client.redis.HSet(ctx, "inflight_tokens:simple", task, strings.Repeat("f", 32))
			case "invalid-queue":
				p.f.client.redis.Set(ctx, "scrapes_simple:"+p.f.task.Domain, "corrupt", 0)
			case "live-sql":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET leased_until=now()+interval '1 hour' WHERE id=$1::uuid", p.f.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			before, canonical := snapshot(t, p.f.client), coldCanonicalSnapshot(t, p.f)
			if _, err := applyFirstFixture(t, p, false); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("unsafe legacy adoption admitted", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || canonical != coldCanonicalSnapshot(t, p.f) || firstFixtureState(t, p) != "staged" {
				t.Fatal("refused adoption partially changed queues or canonical ownership")
			}
		})
	}
}
