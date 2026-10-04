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

func firstWorkdayDetailFixture(t *testing.T) firstOwnerFixture {
	t.Helper()
	p := firstOwnershipFixture(t)
	f, ctx := p.f, context.Background()
	const source = "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001"
	if _, err := f.observer.Exec(ctx, `UPDATE job_board SET board_url='https://fixture.wd1.myworkdayjobs.com/Careers',
 crawler_type='workday',throttle_key='workday',metadata='{"scraper_type":"workday"}'::jsonb WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.task.ID, source); err != nil {
		t.Fatal(err)
	}
	config := workdayDetailConfig()
	config["board_url"] = "https://fixture.wd1.myworkdayjobs.com/Careers"
	config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
	config["domain"], config["throttle_key"] = "workday", "workday"
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.Del(ctx, "monitors_simple:greenhouse", "ft_monitors_simple:greenhouse").Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZRem(ctx, "ready:simple:1", "greenhouse").Err(); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: f.task.ID, BoardID: f.task.ID, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
		t.Fatal(err)
	}
	plan, err := f.authority.StageOwnership(ctx, strings.Repeat("a", 40), []string{f.task.ID}, []string{f.task.ID})
	if err != nil {
		t.Fatal(err)
	}
	p.plan = plan
	p.f.task = &Task{Worker: Simple, Kind: Scrape, ID: f.task.ID, Domain: "fixture.wd1.myworkdayjobs.com"}
	return p
}

func TestRealFirstWorkdayDetailRetirementConservesInterruptedAndCompletedAttempts(t *testing.T) {
	for _, mode := range []string{"active", "claim-before-sql", "committed-before-ack", "reaped-before-ack", "inactive", "save-failure"} {
		t.Run(mode, func(t *testing.T) {
			p := firstWorkdayDetailFixture(t)
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			switch mode {
			case "claim-before-sql":
				if _, err := p.f.observer.Exec(ctx, "DELETE FROM ordinary_worker_write_fence WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID); err != nil {
					t.Fatal(err)
				}
			case "committed-before-ack", "reaped-before-ack", "save-failure":
				detail, err := a.ReadWorkdayDetail(ctx, claim)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := a.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
					_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Native detail'],next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
					return err
				}); err != nil {
					t.Fatal(err)
				}
				if mode == "reaped-before-ack" {
					if err := p.f.client.redis.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: inflight(&claim.task)}).Err(); err != nil {
						t.Fatal(err)
					}
					if _, err := guardedReap(t, p.f); err != nil {
						t.Fatal(err)
					}
				}
			case "inactive":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", claim.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			var due *time.Time
			if err := p.f.observer.QueryRow(ctx, "SELECT CASE WHEN is_active THEN next_scrape_at END FROM job_posting WHERE id=$1::uuid", claim.task.ID).Scan(&due); err != nil {
				t.Fatal(err)
			}
			before := coldCanonicalSnapshot(t, p.f)
			if mode == "save-failure" {
				firstFixtureSave(t, p, false)
				if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrObservation) {
					t.Fatal("SAVE failure did not retain ownership", err)
				}
				if firstFixtureState(t, p) != "active" {
					t.Fatal("unpersisted retirement lost SQL owner")
				}
				firstFixtureSave(t, p, true)
			}
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("full detail retirement failed", err)
			}
			if before != coldCanonicalSnapshot(t, p.f) {
				t.Fatal("detail retirement rewrote canonical content/receipts")
			}
			if p.f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || p.f.client.redis.HLen(ctx, "inflight_tokens:simple").Val() != 0 {
				t.Fatal("retirement retained detail lease/token")
			}
			score, err := p.f.client.redis.ZScore(ctx, "scrapes_simple:"+claim.task.Domain, claim.task.ID).Result()
			if due == nil {
				if err != redis.Nil {
					t.Fatal("inactive detail rescheduled")
				}
			} else if err != nil || score != seconds(*due) {
				t.Fatal("canonical detail deadline lost", err, score, seconds(*due))
			}
			if err := a.Heartbeat(ctx, claim); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retired detail retained heartbeat", err)
			}
			if _, err := a.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { t.Fatal("retired detail wrote"); return nil }); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("retired writer granted", err)
			}
			restartPublicationRedisWithoutSave(t, p.f.client)
			if p.f.client.redis.Exists(ctx, ownershipProjectionKey).Val() != 0 {
				t.Fatal("retirement did not survive Redis reload")
			}
			firstFixtureSave(t, p, false)
			if _, err := applyFirstFixture(t, p, true); err != nil {
				t.Fatal("completed retirement repeated SAVE", err)
			}
		})
	}
}

func TestRealFirstWorkdayDetailRetirementRejectsForeignCanonicalPostingBoard(t *testing.T) {
	p := firstWorkdayDetailFixture(t)
	_, claim := firstRetirementClaim(t, p)
	ctx := context.Background()
	before := snapshot(t, p.f.client)
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid WHERE id=$1::uuid", claim.task.ID, p.target.document.Boards[0].ID); err != nil {
		t.Fatal(err)
	}
	if _, err := applyFirstFixture(t, p, true); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("cold retirement trusted fence board without canonical posting", err)
	}
	if !reflect.DeepEqual(before, snapshot(t, p.f.client)) || firstFixtureState(t, p) != "active" {
		t.Fatal("foreign detail refusal changed projection/queues/owner")
	}
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_posting SET board_id=$2::uuid WHERE id=$1::uuid", claim.task.ID, p.f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := applyFirstFixture(t, p, true); err != nil {
		t.Fatal("exact canonical detail retry refused", err)
	}
}
