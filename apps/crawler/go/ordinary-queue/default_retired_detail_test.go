package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

func TestRealDefaultRetiredDetailSettlesItsFormerLaneWithoutContentWrite(t *testing.T) {
	for _, mode := range []string{"unscheduled", "inactive", "scheduled", "reactivated", "replaced-token", "changed-config"} {
		t.Run(mode, func(t *testing.T) {
			f := currentWorkdayDetailQueueFixture(t)
			ctx := context.Background()
			r := f.client.redis
			const domain = "fixture.wd1.myworkdayjobs.com"
			pipe := r.TxPipeline()
			pipe.ZRem(ctx, "scrapes_simple:"+domain, f.task.ID)
			pipe.ZRem(ctx, "ready:simple:2", domain)
			pipe.ZAdd(ctx, "scrapes_browser:"+domain, redis.Z{Member: f.task.ID, Score: 1})
			pipe.ZAdd(ctx, "ready:browser:2", redis.Z{Member: domain, Score: 1})
			if _, err := pipe.Exec(ctx); err != nil {
				t.Fatal(err)
			}
			if mode == "inactive" {
				if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			} else if mode != "scheduled" {
				if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			a, err := OpenDefaultAuthority(ctx, f.dsn, f.client, f.epoch)
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(a.Close)
			claim, err := a.Claim(ctx, Browser)
			if err != nil || claim == nil {
				t.Fatal("former-lane claim unavailable", err)
			}
			// Canonical reads retain their existing heartbeat. Apart from that
			// lease extension, refusal must preserve every queue/cache/token.
			unchanged := func(before map[string]string) bool {
				after := snapshot(t, f.client)
				delete(before, "inflight:browser")
				delete(after, "inflight:browser")
				return reflect.DeepEqual(before, after) && r.ZCard(ctx, "inflight:browser").Val() == 1 && r.ZScore(ctx, "inflight:browser", inflight(&claim.task)).Err() == nil
			}
			before := snapshot(t, f.client)
			detail, err := a.ReadWorkdayDetail(ctx, claim)
			if mode == "scheduled" {
				if !errors.Is(err, ErrUnsupportedProfile) || !unchanged(before) {
					t.Fatal("scheduled work bypassed exact claimed route", err)
				}
				return
			}
			if err != nil || detail.Schedulable || detail.Profile().Profile != "workday.cxs-detail/v1" {
				t.Fatal("canonical no-fetch retirement unavailable", err)
			}
			if _, err := a.WriteWorkdayDetail(ctx, detail, func(context.Context, pgx.Tx) error { t.Fatal("retired attempt invoked content writer"); return nil }); err == nil {
				t.Fatal("retired attempt retained content authority")
			}
			switch mode {
			case "reactivated":
				_, err = f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=now()+interval '1 hour' WHERE id=$1::uuid", f.task.ID)
			case "replaced-token":
				err = r.HSet(ctx, "inflight_tokens:browser", inflight(&claim.task), strings.Repeat("f", 32)).Err()
			case "changed-config":
				err = r.HSet(ctx, "board:"+f.task.ID, "check_interval_minutes", "121").Err()
			}
			if err != nil {
				t.Fatal(err)
			}
			before = snapshot(t, f.client)
			receipt, err := a.FinishWorkdayDetail(ctx, detail, nil, GreenhouseHostObservation{}, func(ctx context.Context, tx pgx.Tx, current *CurrentWorkdayDetail, _ *HostCircuitOutcome) (string, error) {
				if mode != "unscheduled" && mode != "inactive" {
					t.Fatal("changed authority reached retirement callback")
				}
				if current.Schedulable {
					t.Fatal("reactivated posting retired")
				}
				_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL,leased_until=NULL WHERE id=$1::uuid", current.PostingID())
				return "unscheduled", err
			})
			if mode != "unscheduled" && mode != "inactive" {
				if err == nil || !unchanged(before) {
					t.Fatal("changed authority retirement had effects", err)
				}
				return
			}
			if err != nil || receipt == nil || receipt.NextDue() != nil {
				t.Fatal("retired receipt lost canonical disposition", err)
			}
			if err = a.Settle(ctx, claim, receipt); err != nil {
				t.Fatal(err)
			}
			var title string
			var hash *int64
			var active bool
			var due *time.Time
			var failures int
			if err = f.observer.QueryRow(ctx, "SELECT titles[1],description_r2_hash,is_active,next_scrape_at,scrape_failures FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&title, &hash, &active, &due, &failures); err != nil {
				t.Fatal(err)
			}
			if title != "Original" || hash == nil || *hash != 123 || active != (mode != "inactive") || due != nil || failures != 0 {
				t.Fatal("retirement changed inventory/content/failure budget")
			}
			if r.ZCard(ctx, "inflight:browser").Val() != 0 || r.HExists(ctx, "inflight_tokens:browser", inflight(&claim.task)).Val() || r.Exists(ctx, "scrape:"+f.task.ID).Val() != 0 || r.ZCard(ctx, "scrapes_browser:"+domain).Val() != 0 || r.ZCard(ctx, "scrapes_simple:"+domain).Val() != 0 {
				t.Fatal("retired source representation survived")
			}
		})
	}
}
