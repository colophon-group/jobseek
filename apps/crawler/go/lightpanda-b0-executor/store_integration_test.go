package executor

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
)

func TestOrdinaryLookupStoreReadOnlyBudgetAndAttribution(t *testing.T) {
	dsn := os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL")
	if dsn == "" {
		dsn = os.Getenv("JOBSEEK_ORDINARY_QUEUE_TEST_DATABASE_URL")
	}
	if dsn == "" {
		if os.Getenv("JOBSEEK_ORDINARY_QUEUE_REQUIRE_POSTGRES") == "1" {
			t.Fatal("mandatory migrated ordinary database unavailable")
		}
		t.Skip("requires isolated migrated executor or ordinary database")
	}
	parsed, err := url.Parse(dsn)
	if err != nil || (!strings.HasSuffix(parsed.Path, "_b0_executor_test") && !strings.HasSuffix(parsed.Path, "_ordinary_worker_test")) || (parsed.Hostname() != "127.0.0.1" && parsed.Hostname() != "localhost") {
		t.Fatal("ordinary lookup test requires isolated local executor database")
	}
	ctx := context.Background()
	store, err := OpenOrdinaryLookupStore(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	config := store.pool.Config()
	var application, timeout, readOnly string
	if err := store.pool.QueryRow(ctx, "SELECT current_setting('application_name'),current_setting('statement_timeout'),current_setting('default_transaction_read_only')").Scan(&application, &timeout, &readOnly); err != nil {
		t.Fatal(err)
	}
	if config.MinConns != 1 || config.MaxConns != 1 || application != "jobseek:crawler:ordinary-lookups:local" || timeout != "30s" || readOnly != "on" {
		t.Fatal("ordinary lookup attribution/reader budget changed")
	}
	_, err = store.pool.Exec(ctx, "UPDATE public.job_board SET updated_at=updated_at WHERE false")
	var failure *pgconn.PgError
	if !errors.As(err, &failure) || failure.Code != "25006" {
		t.Fatal("ordinary lookup reader accepted a posting/board writer")
	}
}

func fixtureID(t *testing.T) string {
	t.Helper()
	var bytes [16]byte
	if _, err := rand.Read(bytes[:]); err != nil {
		t.Fatal(err)
	}
	return fmt.Sprintf("%x-%x-%x-%x-%x", bytes[:4], bytes[4:6], bytes[6:8], bytes[8:10], bytes[10:])
}

func fixture(t *testing.T) (*Store, Fence, string) {
	t.Helper()
	dsn := os.Getenv("JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("set isolated migrated JOBSEEK_B0_EXECUTOR_TEST_DATABASE_URL")
	}
	parsed, err := url.Parse(dsn)
	if err != nil || !strings.HasSuffix(parsed.Path, "_b0_executor_test") {
		t.Fatal("executor integration tests require an isolated *_b0_executor_test database")
	}
	ctx := context.Background()
	store, err := OpenStore(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	company, board, posting := fixtureID(t), fixtureID(t), fixtureID(t)
	if _, err = store.pool.Exec(ctx, "INSERT INTO company(id,name,slug) VALUES ($1,'Native executor fixture',$2)", company, "native-"+company); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _, _ = store.pool.Exec(context.Background(), "DELETE FROM company WHERE id=$1", company) })
	if _, err = store.pool.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url) VALUES ($1,$2,$3,$4)", board, company, "native-"+board, "https://native-executor.invalid/"+board); err != nil {
		t.Fatal(err)
	}
	if _, err = store.pool.Exec(ctx, "INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,next_scrape_at) VALUES ($1,$2,$3,$4,ARRAY['Original title'],ARRAY['fr'],now())", posting, company, board, "https://native-executor.invalid/"+posting); err != nil {
		t.Fatal(err)
	}
	var epoch int64
	if err = store.pool.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	f := Fence{PostingID: posting, ShardID: "lightpanda-b0", RoutingEpoch: epoch, ConfigRevision: 1, PayloadSHA256: strings.Repeat("a", 64), ClaimToken: fmt.Sprintf("%d:1", epoch)}
	return store, f, board
}

func TestPostgresFencedContentAndExactDescriptionDeduplication(t *testing.T) {
	store, f, _ := fixture(t)
	ctx := context.Background()
	if err := store.AttestEpoch(ctx, f.RoutingEpoch); err != nil {
		t.Fatal(err)
	}
	if store.pool.Config().MaxConns != 1 || store.pool.Config().MinConns != 1 {
		t.Fatal("connection budget changed")
	}
	var application, timeout string
	if err := store.pool.QueryRow(ctx, "SELECT current_setting('application_name'),current_setting('statement_timeout')").Scan(&application, &timeout); err != nil {
		t.Fatal(err)
	}
	if application != "jobseek:crawler:lightpanda-b0-executor:local" || timeout != "30s" {
		t.Fatalf("startup guards differ: %q / %q", application, timeout)
	}
	if err := store.Activate(ctx, f); err != nil {
		t.Fatal(err)
	}
	description, err := StageDescription("<p>Café 東京</p>", "fr")
	if err != nil {
		t.Fatal(err)
	}
	var diagnostic *DescriptionDiagnostic
	err = store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error {
		var err error
		diagnostic, err = SaveContent(ctx, tx, f.PostingID, ContentFields{}, description)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	if !diagnostic.UploadScheduled || diagnostic.RowExisted {
		t.Fatalf("unexpected first candidate: %+v", diagnostic)
	}
	var titles, locales []string
	var uploaded bool
	var hash int64
	var updated time.Time
	if err := store.pool.QueryRow(ctx, "SELECT jp.titles,jp.locales,d.r2_uploaded,d.hash,d.updated_at FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id WHERE jp.id=$1", f.PostingID).Scan(&titles, &locales, &uploaded, &hash, &updated); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(titles, []string{"Original title"}) || !reflect.DeepEqual(locales, []string{"fr"}) || uploaded || hash != description.Hash {
		t.Fatal("COALESCE retention or description bytes differ")
	}
	if err := store.Activate(ctx, f); !errors.Is(err, ErrAuthorityLost) {
		t.Fatalf("committed duplicate replay accepted: %v", err)
	}
	if _, err := store.pool.Exec(ctx, "UPDATE descriptions SET r2_uploaded=true WHERE posting_id=$1", f.PostingID); err != nil {
		t.Fatal(err)
	}
	f.ClaimToken = fmt.Sprintf("%d:2", f.RoutingEpoch)
	if err := store.Activate(ctx, f); err != nil {
		t.Fatal(err)
	}
	err = store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error {
		var err error
		diagnostic, err = SaveContent(ctx, tx, f.PostingID, ContentFields{}, description)
		return err
	})
	if err != nil {
		t.Fatal(err)
	}
	if diagnostic.UploadScheduled || !diagnostic.RowExisted {
		t.Fatal("equal HTML scheduled another upload")
	}
	var after time.Time
	if err := store.pool.QueryRow(ctx, "SELECT r2_uploaded,updated_at FROM descriptions WHERE posting_id=$1", f.PostingID).Scan(&uploaded, &after); err != nil {
		t.Fatal(err)
	}
	if !uploaded || !after.Equal(updated) {
		t.Fatal("equal HTML erased completed upload or timestamp")
	}
}

func TestPostgresRollbackTimeoutAndStaleEpoch(t *testing.T) {
	store, f, _ := fixture(t)
	ctx := context.Background()
	if err := store.Activate(ctx, f); err != nil {
		t.Fatal(err)
	}
	cause := errors.New("crash before posting commit")
	err := store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Uncommitted'] WHERE id=$1", f.PostingID); err != nil {
			return err
		}
		return cause
	})
	if !errors.Is(err, cause) {
		t.Fatalf("callback error lost: %v", err)
	}
	assertOriginal := func() {
		t.Helper()
		var titles []string
		if err := store.pool.QueryRow(ctx, "SELECT titles FROM job_posting WHERE id=$1", f.PostingID).Scan(&titles); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(titles, []string{"Original title"}) {
			t.Fatal("uncommitted data escaped transaction")
		}
	}
	assertOriginal()
	short, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
	defer cancel()
	err = store.AuthoritativeWrite(short, f, func(tx pgx.Tx) error {
		if _, err := tx.Exec(short, "UPDATE job_posting SET titles=ARRAY['Timed out'] WHERE id=$1", f.PostingID); err != nil {
			return err
		}
		_, err := tx.Exec(short, "SELECT pg_sleep(2)")
		return err
	})
	if err == nil {
		t.Fatal("deadline committed incomplete transaction")
	}
	assertOriginal()
	var epoch int64
	if err := store.pool.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	err = store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error {
		_, err := tx.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Stale epoch'] WHERE id=$1", f.PostingID)
		return err
	})
	if !errors.Is(err, ErrAuthorityLost) {
		t.Fatalf("retired epoch accepted: %v", err)
	}
	assertOriginal()
}

func TestPostgresNativeParameterTypesAndMissingPostingRace(t *testing.T) {
	store, f, _ := fixture(t)
	ctx := context.Background()
	if err := store.Activate(ctx, f); err != nil {
		t.Fatal(err)
	}
	employment, currency, period := "contract", "EUR", "year"
	minimum, maximum, eur := int64(45000), int64(55000), int64(50000)
	experience := 2.5
	fields := ContentFields{EmploymentType: &employment, Titles: []string{"Native title"}, Locales: []string{"en", "fr"}, LocationIDs: []int64{1}, LocationTypes: []string{"onsite"}, TechnologyIDs: []int64{2}, SalaryMin: &minimum, SalaryMax: &maximum, SalaryCurrency: &currency, SalaryPeriod: &period, SalaryEUR: &eur, ExperienceMin: &experience}
	if err := store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error { _, err := SaveContent(ctx, tx, f.PostingID, fields, nil); return err }); err != nil {
		t.Fatal(err)
	}
	var actualMinimum, actualMaximum, actualEUR int64
	var actualExperience float64
	var ids []int64
	if err := store.pool.QueryRow(ctx, "SELECT salary_min,salary_max,salary_eur,experience_min,location_ids FROM job_posting WHERE id=$1", f.PostingID).Scan(&actualMinimum, &actualMaximum, &actualEUR, &actualExperience, &ids); err != nil {
		t.Fatal(err)
	}
	if actualMinimum != minimum || actualMaximum != maximum || actualEUR != eur || actualExperience != experience || !reflect.DeepEqual(ids, []int64{1}) {
		t.Fatal("native SQL codecs changed fields")
	}
	missing := fixtureID(t)
	if err := pgx.BeginFunc(ctx, store.pool, func(tx pgx.Tx) error { _, err := SaveContent(ctx, tx, missing, ContentFields{}, nil); return err }); err == nil {
		t.Fatal("ordinary detail save accepted absent posting")
	}
	// The legacy enrich path allows a posting deleted between current-detail
	// validation and an empty-description save to produce no effects.
	if err := pgx.BeginFunc(ctx, store.pool, func(tx pgx.Tx) error { _, err := SaveEnrichment(ctx, tx, missing, ContentFields{}, nil); return err }); err != nil {
		t.Fatal(err)
	}
}

func TestPostgresFailurePolicyAndNeverRescrape(t *testing.T) {
	for _, disposition := range []FailureDisposition{FailureTransient, FailureBudget, FailureGone} {
		t.Run(string(disposition), func(t *testing.T) {
			store, f, _ := fixture(t)
			ctx := context.Background()
			for attempt := int64(1); attempt <= 3; attempt++ {
				f.ClaimToken = fmt.Sprintf("%d:%d", f.RoutingEpoch, attempt)
				if err := store.Activate(ctx, f); err != nil {
					t.Fatal(err)
				}
				if err := store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error { return RecordFailure(ctx, tx, f.PostingID, disposition) }); err != nil {
					t.Fatal(err)
				}
				schedule, err := store.ReadSchedule(ctx, f.PostingID)
				if err != nil {
					t.Fatal(err)
				}
				gone := disposition == FailureGone || (disposition == FailureBudget && attempt == 3)
				if schedule.IsActive == gone {
					t.Fatal("failure policy changed posting visibility")
				}
				if (schedule.NextScrapeAt == nil) != (disposition == FailureGone || attempt == 3) {
					t.Fatal("failure budget changed scheduling")
				}
			}
		})
	}
	t.Run("never", func(t *testing.T) {
		store, f, board := fixture(t)
		ctx := context.Background()
		if _, err := store.pool.Exec(ctx, `UPDATE job_board SET metadata='{"rescrape_policy":"never"}'::jsonb WHERE id=$1`, board); err != nil {
			t.Fatal(err)
		}
		if err := store.Activate(ctx, f); err != nil {
			t.Fatal(err)
		}
		if err := store.AuthoritativeWrite(ctx, f, func(tx pgx.Tx) error { _, err := SaveContent(ctx, tx, f.PostingID, ContentFields{}, nil); return err }); err != nil {
			t.Fatal(err)
		}
		schedule, err := store.ReadSchedule(ctx, f.PostingID)
		if err != nil {
			t.Fatal(err)
		}
		if !schedule.IsActive || schedule.NextScrapeAt != nil {
			t.Fatal("never-rescrape policy differs")
		}
	})
}
