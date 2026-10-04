package queue

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func currentWorkdayDetailFixture(t *testing.T) (authorityFixture, *Claim) {
	t.Helper()
	f := realAuthority(t, Scrape, Simple, ordinaryID(t))
	ctx := context.Background()
	const source = "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001"
	if _, err := f.observer.Exec(ctx, `UPDATE job_board SET board_url='https://fixture.wd1.myworkdayjobs.com/Careers',
 crawler_type='workday',throttle_key='workday',check_interval_minutes=60,scrape_interval_hours=24,
 metadata='{"scraper_type":"workday"}'::jsonb WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2 WHERE id=$1::uuid", f.task.ID, source); err != nil {
		t.Fatal(err)
	}
	config := workdayDetailConfig()
	config["board_url"] = "https://fixture.wd1.myworkdayjobs.com/Careers"
	config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
	config["domain"], config["throttle_key"] = "workday", "workday"
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, config).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.Del(ctx, "scrapes_simple:"+f.task.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.ZRem(ctx, "ready:simple:2", f.task.Domain).Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.Del(ctx, "scrape:"+f.task.ID).Err(); err != nil {
		t.Fatal(err)
	}
	hash := int64(123)
	if _, err := f.client.EnqueueURLDetail(ctx, URLOnlyDetail{ID: f.task.ID, BoardID: f.task.ID, URL: source, DescriptionHash: &hash, Due: time.Now().Add(-time.Minute)}); err != nil {
		t.Fatal(err)
	}
	return f, mustClaim(t, f)
}

func TestRealWorkdayDetailCanonicalObservationAndTerminalReceipt(t *testing.T) {
	f, claim := currentWorkdayDetailFixture(t)
	ctx := context.Background()
	detail, err := f.authority.ReadWorkdayDetail(ctx, claim)
	if err != nil || detail == nil || !detail.Schedulable || detail.PublisherReserved || detail.DescriptionHash == nil || *detail.DescriptionHash != 123 || len(detail.Titles) != 1 || detail.Titles[0] != "Original" {
		t.Fatalf("canonical detail unavailable: %+v / %v", detail, err)
	}
	receipt, err := f.authority.WriteWorkdayDetail(ctx, detail, func(ctx context.Context, tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Native detail'],next_scrape_at=now()+interval '24 hours' WHERE id=$1::uuid", claim.task.ID)
		return err
	})
	if err != nil || receipt == nil || receipt.NextDue() == nil {
		t.Fatal("detail content/deadline not committed under one receipt", err)
	}
	if err := f.authority.Settle(ctx, claim, receipt); err != nil {
		t.Fatal("detail settlement failed", err)
	}
	score, err := f.client.redis.ZScore(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", claim.task.ID).Result()
	if err != nil || score != seconds(*receipt.NextDue()) || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("canonical detail deadline and existing queue differ", err)
	}
	if _, err := f.authority.WriteWorkdayDetail(ctx, detail, func(context.Context, pgx.Tx) error { t.Fatal("settled attempt wrote again"); return nil }); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("completed detail attempt retained write authority", err)
	}
}

func TestRealWorkdayDetailRevalidatesMutableCanonicalStateBeforeWrite(t *testing.T) {
	for _, mode := range []string{"source", "board_options", "reserved", "inactive", "unscheduled", "lease_ended"} {
		t.Run(mode, func(t *testing.T) {
			f, claim := currentWorkdayDetailFixture(t)
			ctx := context.Background()
			detail, err := f.authority.ReadWorkdayDetail(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			sql := ""
			switch mode {
			case "source":
				sql = "UPDATE job_posting SET source_url=source_url||'/changed' WHERE id=$1::uuid"
			case "board_options":
				sql = `UPDATE job_board SET metadata='{"scraper_type":"workday","scraper_config":{"facility_tenant_aliases":["new"]}}'::jsonb WHERE id=$1::uuid`
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"scraper_type":"workday","scraper_config":{"facility_tenant_aliases":["new"]}}`).Err(); err != nil {
					t.Fatal(err)
				}
			case "reserved":
				sql = "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid"
			case "inactive":
				sql = "UPDATE job_posting SET is_active=false WHERE id=$1::uuid"
			case "unscheduled":
				sql = "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid"
			case "lease_ended":
				if ok, err := f.client.Complete(ctx, &claim.task); err != nil || !ok {
					t.Fatal("private lease retirement failed", err)
				}
			}
			if sql != "" {
				if _, err := f.observer.Exec(ctx, sql, f.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			_, err = f.authority.WriteWorkdayDetail(ctx, detail, func(context.Context, pgx.Tx) error {
				t.Fatal("stale/reserved detail invoked the SQL writer")
				return nil
			})
			want := ErrAuthorityLost
			if mode == "reserved" {
				want = ErrPublisherReserved
			}
			if !errors.Is(err, want) {
				t.Fatalf("changed %s detail retained authority: %v", mode, err)
			}
		})
	}
}
