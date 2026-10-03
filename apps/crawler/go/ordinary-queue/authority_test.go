package queue

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestOrdinaryAuthoritySchemaMatchesAppliedMigration(t *testing.T) {
	body, err := os.ReadFile("../../src/migrations/sql/ordinary_worker_write_fence.sql")
	if err != nil || string(body) != authoritySchema {
		t.Fatal("native SQL differs from applied migration")
	}
}

func ordinaryID(t *testing.T) string {
	t.Helper()
	var value [16]byte
	if _, err := rand.Read(value[:]); err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%x-%x-%x-%x-%x", value[:4], value[4:6], value[6:8], value[8:10], value[10:])
}

type authorityFixture struct {
	authority *Authority
	client    *Client
	observer  *pgxpool.Pool
	dsn       string
	epoch     int64
	task      *Task
	company   string
}

func realAuthority(t *testing.T, kind Kind, worker WorkerType) authorityFixture {
	t.Helper()
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required migrated private PG fixture unavailable")
		}
		t.Skip("set migrated isolated *_ordinary_worker_test fixture")
	}
	parsed, err := url.Parse(dsn)
	if err != nil || !strings.HasSuffix(parsed.Path, "_ordinary_worker_test") || (parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "localhost") {
		t.Fatal("ordinary authority needs a private local test database")
	}
	config, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid fixture connection")
	}
	config.MaxConns, config.MinConns = 4, 0
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:fixture:ordinary-observer"
	pool, err := pgxpool.NewWithConfig(context.Background(), config)
	if err != nil {
		t.Fatal("fixture observer unavailable")
	}
	t.Cleanup(pool.Close)
	c := privateRedis(t)
	task, _ := seedTask(t, c, kind, worker)
	company := ordinaryID(t)
	ctx := context.Background()
	if _, err := pool.Exec(ctx, "INSERT INTO company(id,slug,name) VALUES($1::uuid,$2,'Ordinary authority fixture')", company, "ordinary-"+company); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,next_check_at)
  VALUES($1::uuid,$2::uuid,$3,$4,'greenhouse',clock_timestamp()-interval '1 minute')`, task.ID, company, "ordinary-"+company, "https://ordinary-worker.invalid/"+company); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,description_r2_hash,next_scrape_at)
  VALUES($1::uuid,$2::uuid,$1::uuid,$3,ARRAY['Original'],ARRAY['en'],123,clock_timestamp()-interval '1 minute')`, task.ID, company, "https://ordinary-worker.invalid/posting/"+company); err != nil {
		t.Fatal(err)
	}
	if _, err := pool.Exec(ctx, "INSERT INTO descriptions(posting_id,locale,html,hash,r2_uploaded) VALUES($1::uuid,'en','<p>Before</p>',123,true)", task.ID); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_, _ = pool.Exec(ctx, "DELETE FROM descriptions WHERE posting_id=$1::uuid", task.ID)
		_, _ = pool.Exec(ctx, "DELETE FROM job_posting WHERE id=$1::uuid", task.ID)
		_, _ = pool.Exec(ctx, "DELETE FROM job_board WHERE id=$1::uuid", task.ID)
		_, _ = pool.Exec(ctx, "DELETE FROM company WHERE id=$1::uuid", company)
	})
	var epoch int64
	if err := pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", routingEpochBarrier); err != nil {
			return err
		}
		return tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch)
	}); err != nil {
		t.Fatal(err)
	}
	a, err := OpenAuthority(ctx, dsn, c, epoch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	return authorityFixture{a, c, pool, dsn, epoch, task, company}
}
func mustClaim(t *testing.T, f authorityFixture) *Claim {
	t.Helper()
	claim, err := f.authority.Claim(context.Background(), f.task.Worker)
	if err != nil || claim == nil {
		t.Fatalf("real claim activation failed: %v", err)
	}
	return claim
}
func guardedReap(t *testing.T, f authorityFixture) ([]any, error) {
	t.Helper()
	var result []any
	err := WithOrdinaryLeaseRetirement(context.Background(), f.observer, func(ctx context.Context) error {
		now, err := f.client.clock(ctx)
		if err != nil {
			return err
		}
		result, err = f.client.redis.Eval(ctx, reaperSource(t), nil, string(f.task.Worker), number(now), 10, 3, number(now), "guarded").Slice()
		return err
	})
	return result, err
}
func effect(ctx context.Context, tx pgx.Tx, claim *Claim, due time.Time) error {
	if _, err := tx.Exec(ctx, `UPDATE job_posting SET titles=ARRAY['Native authority'],description_r2_hash=456,next_scrape_at=$2 WHERE id=$1::uuid`, claim.task.ID, due); err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, `UPDATE descriptions SET html='<p>Native authority</p>',hash=456,r2_uploaded=false WHERE posting_id=$1::uuid`, claim.task.ID); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `UPDATE job_board SET next_check_at=$2,empty_check_count=empty_check_count+1 WHERE id=$1::uuid`, claim.boardID, due)
	return err
}
func assertCanonical(t *testing.T, f authorityFixture, changed bool) {
	t.Helper()
	var title, html string
	var hash, descriptionHash int64
	var uploaded bool
	var count int
	err := f.observer.QueryRow(context.Background(), `SELECT jp.titles[1],jp.description_r2_hash,d.html,d.hash,d.r2_uploaded,jb.empty_check_count
  FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id JOIN job_board jb ON jb.id=jp.board_id WHERE jp.id=$1::uuid`, f.task.ID).Scan(&title, &hash, &html, &descriptionHash, &uploaded, &count)
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		if title != "Native authority" || html != "<p>Native authority</p>" || hash != 456 || descriptionHash != 456 || uploaded || count != 1 {
			t.Fatal("committed canonical/description/board effects differ")
		}
	} else if title != "Original" || html != "<p>Before</p>" || hash != 123 || descriptionHash != 123 || !uploaded || count != 0 {
		t.Fatal("uncommitted effects escaped")
	}
}
func futureDue() time.Time { return time.Now().UTC().Truncate(time.Microsecond).Add(time.Hour) }

func TestRealAuthorityCanonicalCommitAndExactSettlement(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		for _, worker := range []WorkerType{Simple, Browser} {
			t.Run(string(worker)+"/"+string(kind), func(t *testing.T) {
				f := realAuthority(t, kind, worker)
				claim := mustClaim(t, f)
				ctx := context.Background()
				due := futureDue()
				descriptor := claim.Descriptor()
				descriptor.Config["crawler_type"] = "mutated-caller-copy"
				receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
					if deadline, ok := ctx.Deadline(); !ok || time.Until(deadline) > 15*time.Second {
						return errors.New("writer transaction is not bounded")
					}
					return effect(ctx, tx, claim, due)
				})
				if err != nil || receipt == nil || !receipt.NextDue().Equal(due) {
					t.Fatalf("terminal transaction failed: %v", err)
				}
				assertCanonical(t, f, true)
				detached := receipt.NextDue()
				*detached = detached.Add(time.Hour)
				if err := f.authority.Settle(ctx, claim, receipt); err != nil {
					t.Fatal(err)
				}
				prefix := "monitors_"
				if kind == Scrape {
					prefix = "scrapes_"
				}
				score, err := f.client.redis.ZScore(ctx, prefix+string(worker)+":"+f.task.Domain, f.task.ID).Result()
				if err != nil || score != seconds(due) {
					t.Fatal("settlement changed PostgreSQL due timestamp")
				}
				if depth, err := f.client.redis.ZCard(ctx, "inflight:"+string(worker)).Result(); err != nil || depth != 0 {
					t.Fatal("settlement retained lease")
				}
				called := false
				if _, err := f.authority.Write(ctx, claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
					t.Fatal("settled attempt wrote twice")
				}
			})
		}
	}
}

func TestRealAuthorityFailureCancellationAndReceiptValidation(t *testing.T) {
	f := realAuthority(t, Scrape, Simple)
	claim := mustClaim(t, f)
	ctx := context.Background()
	due := futureDue()
	cause := errors.New("fixture callback failure")
	if receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		if err := effect(ctx, tx, claim, due); err != nil {
			return err
		}
		return cause
	}); receipt != nil || !errors.Is(err, cause) {
		t.Fatal("callback failure committed")
	}
	assertCanonical(t, f, false)
	short, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	if receipt, err := f.authority.Write(short, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		if err := effect(ctx, tx, claim, due); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "SELECT pg_sleep(2)")
		return err
	}); receipt != nil || err == nil {
		t.Fatal("cancelled transaction committed")
	}
	assertCanonical(t, f, false)
	if err := f.authority.Settle(ctx, claim, &Receipt{claim: claim, nextDue: &due}); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("uncommitted receipt settled")
	}
	receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error { return effect(ctx, tx, claim, due) })
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=$2 WHERE id=$1::uuid", claim.task.ID, due.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	if err := f.authority.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("stale canonical deadline settled")
	}
}

func waitForBarrier(t *testing.T, pool *pgxpool.Pool, key int64) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	for {
		var waiting bool
		err := pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND NOT granted
   AND classid::bigint=$1 AND objid::bigint=$2 AND objsubid=1 AND database=(SELECT oid FROM pg_database WHERE datname=current_database()))`, key>>32, key&0xffffffff).Scan(&waiting)
		if err != nil {
			t.Fatal("barrier waiter observation failed")
		}
		if waiting {
			return
		}
		if ctx.Err() != nil {
			t.Fatal("expected database barrier waiter missing")
		}
		time.Sleep(5 * time.Millisecond)
	}
}
func awaitWrite(t *testing.T, done <-chan error) error {
	t.Helper()
	select {
	case err := <-done:
		return err
	case <-time.After(5 * time.Second):
		t.Fatal("bounded writer did not finish")
	}
	return nil
}

func TestRealAuthorityExpiryRollbackOrdersReaperAndNewAttempt(t *testing.T) {
	f := realAuthority(t, Scrape, Simple)
	claim := mustClaim(t, f)
	entered := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	go func() {
		_, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
			if err := effect(ctx, tx, claim, futureDue()); err != nil {
				return err
			}
			close(entered)
			select {
			case <-release:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			}
		})
		done <- err
	}()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("writer did not enter")
	}
	expire(t, f.client, &claim.task)
	reaped := make(chan error, 1)
	go func() { _, err := guardedReap(t, f); reaped <- err }()
	waitForBarrier(t, f.observer, OrdinaryLeaseBarrier)
	close(release)
	if err := awaitWrite(t, done); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("expired attempt committed")
	}
	if err := awaitWrite(t, reaped); err != nil {
		t.Fatal(err)
	}
	assertCanonical(t, f, false)
	current := mustClaim(t, f)
	if current.task.claimToken == claim.task.claimToken {
		t.Fatal("new attempt reused token")
	}
	before := snapshot(t, f.client)
	called := false
	if _, err := f.authority.Write(context.Background(), claim, true, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
		t.Fatal("stale DB writer accepted")
	}
	if err := f.authority.Heartbeat(context.Background(), claim); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("stale heartbeat accepted")
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("old attempt mutated new lease")
	}
}

func TestRealAuthorityDelayedActivationRejectsExpiredClaim(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	blocker, err := f.observer.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback(context.Background())
	if _, err := blocker.Exec(ctx, "SELECT id FROM job_board WHERE id=$1::uuid FOR UPDATE", f.task.ID); err != nil {
		t.Fatal(err)
	}
	result := make(chan error, 1)
	go func() {
		claim, err := f.authority.Claim(ctx, Simple)
		if claim == nil {
			result <- errors.New("lost partial inflight descriptor")
			return
		}
		result <- err
	}()
	for {
		count, err := f.client.redis.ZCard(ctx, "inflight:simple").Result()
		if err != nil {
			t.Fatal(err)
		}
		if count == 1 {
			break
		}
		if ctx.Err() != nil {
			t.Fatal("claim did not reach blocked DB activation")
		}
		time.Sleep(5 * time.Millisecond)
	}
	expire(t, f.client, f.task)
	if err := blocker.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if err := awaitWrite(t, result); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("delayed expired activation succeeded")
	}
	var count int
	if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&count); err != nil || count != 0 {
		t.Fatal("expired activation persisted fence")
	}
	assertCanonical(t, f, false)
}

func TestRealAuthorityEpochRetirementOrdersCommitAndRejectsOldOwner(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	claim := mustClaim(t, f)
	entered := make(chan struct{})
	release := make(chan struct{})
	done := make(chan error, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	var receipt *Receipt
	go func() {
		var err error
		receipt, err = f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
			if err := effect(ctx, tx, claim, futureDue()); err != nil {
				return err
			}
			close(entered)
			select {
			case <-release:
				return nil
			case <-ctx.Done():
				return ctx.Err()
			}
		})
		done <- err
	}()
	select {
	case <-entered:
	case <-ctx.Done():
		t.Fatal("writer did not enter")
	}
	retired := make(chan error, 1)
	go func() {
		retired <- pgx.BeginFunc(ctx, f.observer, func(tx pgx.Tx) error {
			if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", routingEpochBarrier); err != nil {
				return err
			}
			_, err := tx.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
			return err
		})
	}()
	waitForBarrier(t, f.observer, routingEpochBarrier)
	close(release)
	if err := awaitWrite(t, done); err != nil {
		t.Fatal(err)
	}
	if err := awaitWrite(t, retired); err != nil {
		t.Fatal(err)
	}
	assertCanonical(t, f, true)
	before := snapshot(t, f.client)
	if err := f.authority.Settle(ctx, claim, receipt); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired owner settled")
	}
	if _, err := f.authority.Claim(ctx, Simple); !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("retired owner claimed work")
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("retired owner changed queue")
	}
}

func TestRealAuthorityCommitBeforeAcknowledgementRecovery(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		for _, retire := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/retire=%t", kind, retire), func(t *testing.T) {
				f := realAuthority(t, kind, Simple)
				claim := mustClaim(t, f)
				due := futureDue()
				ctx := context.Background()
				if _, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error { return effect(ctx, tx, claim, due) }); err != nil {
					t.Fatal(err)
				}
				// Model process loss after actual PG commit, before any Redis settlement.
				f.authority.Close()
				expire(t, f.client, &claim.task)
				if _, err := guardedReap(t, f); err != nil {
					t.Fatal(err)
				}
				epoch := f.epoch
				if retire {
					if err := pgx.BeginFunc(ctx, f.observer, func(tx pgx.Tx) error {
						if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", routingEpochBarrier); err != nil {
							return err
						}
						return tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch)
					}); err != nil {
						t.Fatal(err)
					}
				}
				replacement, err := OpenAuthority(ctx, f.dsn, f.client, epoch)
				if err != nil {
					t.Fatal(err)
				}
				defer replacement.Close()
				current, err := replacement.Claim(ctx, Simple)
				if err != nil || current == nil || current.RecoveredReceipt() == nil {
					t.Fatalf("committed attempt not recovered: %v", err)
				}
				if !current.RecoveredReceipt().NextDue().Equal(due) {
					t.Fatal("recovery changed canonical due")
				}
				called := false
				if _, err := replacement.Write(ctx, current, true, func(context.Context, pgx.Tx) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
					t.Fatal("recovered attempt wrote again")
				}
				if err := replacement.Settle(ctx, current, current.RecoveredReceipt()); err != nil {
					t.Fatal(err)
				}
				assertCanonical(t, f, true)
			})
		}
	}
}

func TestRealAuthorityUnscheduledDetailRecovery(t *testing.T) {
	f := realAuthority(t, Scrape, Simple)
	claim := mustClaim(t, f)
	ctx := context.Background()
	receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		if err := effect(ctx, tx, claim, futureDue()); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1::uuid", claim.task.ID)
		return err
	})
	if err != nil || receipt == nil || receipt.NextDue() != nil {
		t.Fatal("unscheduled commit failed")
	}
	expire(t, f.client, &claim.task)
	if _, err := guardedReap(t, f); err != nil {
		t.Fatal(err)
	}
	current := mustClaim(t, f)
	recovered := current.RecoveredReceipt()
	if recovered == nil || recovered.NextDue() != nil {
		t.Fatal("unscheduled commit not recovered")
	}
	if err := f.authority.Settle(ctx, current, recovered); err != nil {
		t.Fatal(err)
	}
	if f.client.redis.Exists(ctx, "scrape:"+claim.task.ID).Val() != 0 || f.client.redis.ZCard(ctx, "inflight:simple").Val() != 0 || f.client.redis.ZCard(ctx, "scrapes_simple:"+claim.task.Domain).Val() != 0 {
		t.Fatal("unscheduled detail retained queue state")
	}
	assertCanonical(t, f, true)
}

func TestRealAuthorityConfigurationChangeRollsBackAndRetiredEpochCannotPersist(t *testing.T) {
	f := realAuthority(t, Monitor, Simple)
	claim := mustClaim(t, f)
	ctx := context.Background()
	receipt, err := f.authority.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		if err := effect(ctx, tx, claim, futureDue()); err != nil {
			return err
		}
		return f.client.redis.HSet(ctx, "board:"+claim.task.ID, "crawler_type", "changed").Err()
	})
	if receipt != nil || !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("configuration change committed")
	}
	assertCanonical(t, f, false)
	if err := pgx.BeginFunc(ctx, f.observer, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", routingEpochBarrier); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')")
		return err
	}); err != nil {
		t.Fatal(err)
	}
	_, err = f.observer.Exec(ctx, "UPDATE ordinary_worker_write_fence SET updated_at=clock_timestamp() WHERE task_id=$1::uuid", claim.task.ID)
	var rejected *pgconn.PgError
	if !errors.As(err, &rejected) || rejected.Code != "P0001" || rejected.Message != "ordinary_worker_write_fence_rejected" {
		t.Fatal("schema accepted retired epoch")
	}
}
