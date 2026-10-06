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
	return firstAPIDetailFixture(t, "workday")
}
func firstAPIDetailFixture(t *testing.T, requested string) firstOwnerFixture {
	t.Helper()
	if requested == "eightfold/proxy" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"eightfold","scraper_config":{"proxy":true,"enrich":["description"]}}`, "https://citi.eightfold.ai/careers/job/859033176537-engineer?domain=citi.com", "citi.eightfold.ai")
	}
	if requested == "paylocity/proxy" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"paylocity","scraper_config":{"proxy":true,"enrich":["description"]}}`, "https://2000recruiting.paylocity.com/Recruiting/Jobs/Details/123", "2000recruiting.paylocity.com")
	}
	if requested == "adp" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"adp","scraper_config":{"enrich":["description"]}}`, "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=01234567-89ab-cdef-0123-456789abcdef&ccId=19000101_000001&lang=en_US&jobId=123_1", "workforcenow.adp.com")
	}
	if requested == "paylocity" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"paylocity","scraper_config":{"enrich":["description"]}}`, "https://2000recruiting.paylocity.com/Recruiting/Jobs/Details/123", "2000recruiting.paylocity.com")
	}
	if requested == "paycom" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"paycom","scraper_config":{"enrich":["description"]}}`, "https://www.paycomonline.net/v4/ats/web.php/portal/0123456789abcdef0123456789abcdef/jobs/123", "www.paycomonline.net")
	}
	if requested == "rippling" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"rippling"}`, "https://ats.rippling.com/acme/jobs/abc-123", "ats.rippling.com")
	}
	if requested == "mokahr" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"mokahr","scraper_config":{"enrich":["description"]}}`, "https://app.mokahr.com/social-recruitment/zte/47588#/job/0c44abe6", "app.mokahr.com")
	}
	if requested == "eightfold" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"eightfold","scraper_config":{"enrich":["description"]}}`, "https://citi.eightfold.ai/careers/job/859033176537-engineer?domain=citi.com", "citi.eightfold.ai")
	}
	if requested == "embedded" {
		return firstIndependentDetailFixture(t, `{"scraper_type":"nextdata","scraper_config":{"path":"job","fields":{"title":"title","description":"description"},"enrich":["description"]}}`, "https://careers.example.net/job/123", "careers.example.net")
	}
	p := firstOwnershipFixture(t)
	f, ctx := p.f, context.Background()
	provider, boardURL, source, metadata := "workday", "https://fixture.wd1.myworkdayjobs.com/Careers", "https://fixture.wd1.myworkdayjobs.com/Careers/job/JR001", `{"scraper_type":"workday"}`
	if requested == "oracle_hcm" {
		provider = "oracle_hcm"
		boardURL = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs"
		source = "https://fixture.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123"
		metadata = `{"scraper_type":"oracle_hcm","scraper_config":{"enrich":["description"]}}`
	}
	if _, err := f.observer.Exec(ctx, `UPDATE job_board SET board_url=$2,crawler_type=$3,throttle_key=$3,metadata=$4::jsonb WHERE id=$1::uuid`, f.task.ID, boardURL, provider, metadata); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.task.ID, source); err != nil {
		t.Fatal(err)
	}
	config := workdayDetailConfig()
	config["board_url"], config["crawler_type"], config["metadata"] = boardURL, provider, metadata
	config["company_id"], config["board_slug"] = f.company, "ordinary-"+f.company
	config["domain"], config["throttle_key"] = provider, provider
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
	p.f.task = &Task{Worker: Simple, Kind: Scrape, ID: f.task.ID, Domain: func() string {
		if provider == "oracle_hcm" {
			return "fixture.fa.em2.oraclecloud.com"
		}
		return "fixture.wd1.myworkdayjobs.com"
	}()}
	return p
}

func TestRealFirstWorkdayDetailRetirementConservesInterruptedAndCompletedAttempts(t *testing.T) {
	testFirstAPIDetailRetirement(t, "workday")
}
func TestRealFirstOracleDetailRetirementConservesInterruptedAndCompletedAttempts(t *testing.T) {
	testFirstAPIDetailRetirement(t, "oracle_hcm")
}
func TestRealFirstEmbeddedDetailRetirementConservesInterruptedAndCompletedAttempts(t *testing.T) {
	testFirstAPIDetailRetirement(t, "embedded")
}
func TestRealFirstProviderBatchDetailRetirementConservesInterruptedAndCompletedAttempts(t *testing.T) {
	for _, provider := range []string{"mokahr", "eightfold", "paycom", "rippling", "adp", "paylocity", "paylocity/proxy", "eightfold/proxy"} {
		t.Run(provider, func(t *testing.T) { testFirstAPIDetailRetirement(t, provider) })
	}
}
func testFirstAPIDetailRetirement(t *testing.T, provider string) {
	for _, mode := range []string{"active", "claim-before-sql", "committed-before-ack", "reaped-before-ack", "inactive", "save-failure"} {
		t.Run(mode, func(t *testing.T) {
			p := firstAPIDetailFixture(t, provider)
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

func TestRealFirstRetirementRetainsHistoricalDetailReceiptAfterPostingDeletion(t *testing.T) {
	old := firstWorkdayDetailFixture(t)
	authority, claim := firstRetirementClaim(t, old)
	ctx := context.Background()
	if _, err := applyFirstFixture(t, old, true); err != nil {
		t.Fatal("old detail owner did not retire", err)
	}
	var before string
	if err := old.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID).Scan(&before); err != nil {
		t.Fatal(err)
	}
	// Later posting maintenance can delete a posting while its immutable retired
	// owner and audit receipt remain. That receipt grants no current authority.
	if _, err := old.f.observer.Exec(ctx, "DELETE FROM job_posting WHERE id=$1::uuid", claim.task.ID); err != nil {
		t.Fatal(err)
	}
	next := firstOwnershipFixtureHistory(t, true)
	firstRetirementClaim(t, next)
	if _, err := applyFirstFixture(t, next, true); err != nil {
		t.Fatal("historical deleted detail blocked next owner reversal", err)
	}
	var after string
	if err := old.f.observer.QueryRow(ctx, "SELECT to_jsonb(f)::text FROM ordinary_worker_write_fence f WHERE task_kind='scrape' AND task_id=$1::uuid", claim.task.ID).Scan(&after); err != nil || after != before {
		t.Fatal("historical detail receipt was rewritten", err)
	}
	if _, err := authority.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { t.Fatal("retired deleted detail wrote"); return nil }); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired detail regained authority", err)
	}
}
