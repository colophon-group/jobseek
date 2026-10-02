package queue

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func hostSQLFixture(t *testing.T) *pgxpool.Pool {
	t.Helper()
	dsn := os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("required private SQL fixture missing")
		}
		t.Skip("explicit migrated private SQL fixture required")
	}
	u, err := url.Parse(dsn)
	if err != nil || !strings.HasSuffix(u.Path, "_ordinary_worker_test") || (u.Hostname() != "127.0.0.1" && u.Hostname() != "localhost") {
		t.Fatal("private SQL fixture only")
	}
	c, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		t.Fatal("fixture config")
	}
	c.MaxConns, c.MinConns = 4, 0
	p, err := pgxpool.NewWithConfig(context.Background(), c)
	if err != nil {
		t.Fatal("fixture pool")
	}
	t.Cleanup(p.Close)
	return p
}

func hostSQLBindingFixture() HostColdSQLBinding {
	return HostColdSQLBinding{strings.Repeat("a", 40), strings.Repeat("b", 64), strings.Repeat("c", 64)}
}

func hostSQLAssertReleased(t *testing.T, p *pgxpool.Pool) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	err := pgx.BeginFunc(ctx, p, func(tx pgx.Tx) error {
		for _, key := range hostSQLBarriers {
			var got bool
			if tx.QueryRow(ctx, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&got) != nil || !got {
				t.Fatal("SQL barrier leaked", key)
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal("released barrier readback", err)
	}
}

func TestHostColdSQLRequiresExplicitBindingAndScopedLiveHandle(t *testing.T) {
	if WithHostColdSQL(context.Background(), nil, hostSQLBindingFixture(), func(context.Context, *HostColdSQL) error { return nil }) != ErrConfiguration {
		t.Fatal("missing connection admitted")
	}
	if (&HostColdSQL{}).Check(context.Background()) != ErrAuthorityLost {
		t.Fatal("unscoped SQL receipt admitted")
	}
}

func TestHostColdSQLHoldsAllBarriersAcrossDurableCommitAndRollback(t *testing.T) {
	p := hostSQLFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	name := "host_cold_sql_" + strings.ReplaceAll(ordinaryID(t), "-", "")
	if _, err := p.Exec(ctx, "CREATE TABLE "+name+"(phase text PRIMARY KEY); CREATE SEQUENCE "+name+"_epoch"); err != nil {
		t.Fatal("durability fixture", err)
	}
	t.Cleanup(func() { _, _ = p.Exec(context.Background(), "DROP TABLE "+name+"; DROP SEQUENCE "+name+"_epoch") })
	var scoped context.Context
	var retained *HostColdSQL
	err := WithHostColdSQL(ctx, p, hostSQLBindingFixture(), func(c context.Context, s *HostColdSQL) error {
		scoped, retained = c, s
		for _, key := range hostSQLBarriers {
			if err := pgx.BeginFunc(c, p, func(tx pgx.Tx) error {
				var got bool
				if err := tx.QueryRow(c, "SELECT pg_try_advisory_xact_lock_shared($1)", key).Scan(&got); err != nil {
					return err
				}
				if got {
					t.Fatal("writer entered held SQL barrier", key)
				}
				return nil
			}); err != nil {
				return err
			}
		}
		if err := coldTransitionTransaction(c, p, func(c context.Context, tx pgx.Tx) error {
			_, err := tx.Exec(c, "INSERT INTO "+name+" VALUES('intent')")
			return err
		}); err != nil {
			return err
		}
		var count int
		if err := p.QueryRow(c, "SELECT count(*) FROM "+name).Scan(&count); err != nil || count != 1 {
			t.Fatal("intent not independently committed", err, count)
		}
		if err := coldTransitionTransaction(c, p, func(c context.Context, tx pgx.Tx) error {
			if _, err := tx.Exec(c, "SELECT nextval('"+name+"_epoch')"); err != nil {
				return err
			}
			if _, err := tx.Exec(c, "INSERT INTO "+name+" VALUES('uncommitted')"); err != nil {
				return err
			}
			return ErrAuthorityLost
		}); !errors.Is(err, ErrAuthorityLost) {
			t.Fatal("phase rollback not retained", err)
		}
		var called bool
		var epoch int64
		if err := p.QueryRow(c, "SELECT last_value,is_called FROM "+name+"_epoch").Scan(&epoch, &called); err != nil || !called || epoch != 1 {
			t.Fatal("nontransactional epoch behavior changed", err)
		}
		if err := p.QueryRow(c, "SELECT count(*) FROM "+name).Scan(&count); err != nil || count != 1 {
			t.Fatal("rollback lost earlier durable intent", err)
		}
		if err := coldTransitionTransaction(c, p, func(c context.Context, tx pgx.Tx) error {
			_, err := tx.Exec(c, "INSERT INTO "+name+" VALUES('reserved')")
			return err
		}); err != nil {
			return err
		}
		if s.Check(c) != nil || s.Body() == "" || strings.Contains(s.Body(), "postgresql://") {
			t.Fatal("scoped SQL proof lost/leaked")
		}
		var body struct {
			Runtime bool    `json:"runtime_admission"`
			Keys    []int64 `json:"exclusive_barriers"`
		}
		if json.Unmarshal([]byte(s.Body()), &body) != nil || body.Runtime || len(body.Keys) != 3 {
			t.Fatal("SQL observation scope changed")
		}
		return nil
	})
	if err != nil {
		t.Fatal("committed cold scope", err)
	}
	if retained.Check(scoped) != ErrAuthorityLost {
		t.Fatal("past receipt remained live authority")
	}
	hostSQLAssertReleased(t, p)
}

func TestHostColdSQLRefusesLiveLegacyLeasesWithoutClearingThem(t *testing.T) {
	p := hostSQLFixture(t)
	ctx := context.Background()
	company, board := ordinaryID(t), ordinaryID(t)
	if _, err := p.Exec(ctx, "INSERT INTO company(id,slug,name) VALUES($1,$2,'SQL lease fixture')", company, "sql-"+company); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = p.Exec(ctx, "DELETE FROM job_posting WHERE board_id=$1", board)
		_, _ = p.Exec(ctx, "DELETE FROM job_board WHERE id=$1", board)
		_, _ = p.Exec(ctx, "DELETE FROM company WHERE id=$1", company)
	})
	if _, err := p.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url,crawler_type,leased_until,lease_owner) VALUES($1,$2,$3,'https://sql-fixture.invalid','greenhouse',clock_timestamp()+interval '1 minute','private-fixture-owner')", board, company, "sql-"+board); err != nil {
		t.Fatal(err)
	}
	called := false
	err := WithHostColdSQL(ctx, p, hostSQLBindingFixture(), func(context.Context, *HostColdSQL) error { called = true; return nil })
	if !errors.Is(err, ErrAuthorityLost) || called {
		t.Fatal("live lease admitted", err)
	}
	var owner string
	var live bool
	if p.QueryRow(ctx, "SELECT lease_owner,leased_until>clock_timestamp() FROM job_board WHERE id=$1", board).Scan(&owner, &live) != nil || owner != "private-fixture-owner" || !live {
		t.Fatal("refusal changed lease")
	}
	if _, err := p.Exec(ctx, "UPDATE job_board SET leased_until=clock_timestamp()-interval '1 second' WHERE id=$1", board); err != nil {
		t.Fatal(err)
	}
	if err := WithHostColdSQL(ctx, p, hostSQLBindingFixture(), func(context.Context, *HostColdSQL) error { return nil }); err != nil {
		t.Fatal("expired lease rejected", err)
	}
	posting := ordinaryID(t)
	if _, err := p.Exec(ctx, "INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,leased_until) VALUES($1,$2,$3,$4,ARRAY['SQL fixture'],ARRAY['en'],clock_timestamp()+interval '1 minute')", posting, company, board, "https://sql-fixture.invalid/"+posting); err != nil {
		t.Fatal("posting lease fixture", err)
	}
	called = false
	if err := WithHostColdSQL(ctx, p, hostSQLBindingFixture(), func(context.Context, *HostColdSQL) error { called = true; return nil }); !errors.Is(err, ErrAuthorityLost) || called {
		t.Fatal("live posting lease admitted", err)
	}
	if err := p.QueryRow(ctx, "SELECT leased_until>clock_timestamp() FROM job_posting WHERE id=$1", posting).Scan(&live); err != nil || !live {
		t.Fatal("refusal cleared posting lease", err)
	}
	hostSQLAssertReleased(t, p)
}

func TestHostColdSQLReleasesPartialBarrierSetAndFailureSession(t *testing.T) {
	p := hostSQLFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	blocker, err := p.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Release()
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_lock_shared($1)", hostCDCWriterBarrier); err != nil {
		t.Fatal(err)
	}
	defer blocker.Exec(context.Background(), "SELECT pg_advisory_unlock_shared($1)", hostCDCWriterBarrier)
	done := make(chan error, 1)
	go func() {
		done <- WithHostColdSQL(ctx, p, hostSQLBindingFixture(), func(context.Context, *HostColdSQL) error { return ErrAuthorityLost })
	}()
	time.Sleep(80 * time.Millisecond)
	probe, err := p.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer probe.Release()
	for {
		var got bool
		if probe.QueryRow(ctx, "SELECT pg_try_advisory_lock($1)", OrdinaryLeaseBarrier).Scan(&got) != nil {
			t.Fatal("partial set probe")
		}
		if got {
			_, _ = probe.Exec(ctx, "SELECT pg_advisory_unlock($1)", OrdinaryLeaseBarrier)
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("wait retained partial barriers")
		case <-time.After(5 * time.Millisecond):
		}
	}
	if _, err := blocker.Exec(ctx, "SELECT pg_advisory_unlock_shared($1)", hostCDCWriterBarrier); err != nil {
		t.Fatal(err)
	}
	if err := <-done; !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("failed callback grant", err)
	}
	hostSQLAssertReleased(t, p)
}
