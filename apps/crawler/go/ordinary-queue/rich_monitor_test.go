package queue

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

func richDescription(html, locale string) (*GreenhouseRichDescription, error) {
	if html == "" {
		return nil, nil
	}
	digest := sha256.Sum256([]byte(html))
	return &GreenhouseRichDescription{html, locale, int64(binary.BigEndian.Uint64(digest[:8]))}, nil
}

func richPosting(t *testing.T, url, title, html string) GreenhouseRichPosting {
	t.Helper()
	description, err := richDescription(html, "en")
	if err != nil {
		t.Fatal(err)
	}
	return GreenhouseRichPosting{url, &GreenhouseRichContent{
		Fields: GreenhouseRichFields{Titles: []string{title}, Locales: []string{"en"}}, Description: description,
	}}
}

func richClaimFixture(t *testing.T) (authorityFixture, *Authority, *Claim) {
	t.Helper()
	f, a, _ := realOwnedAuthority(t)
	t.Cleanup(func() {
		_, err := f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE company_id=$1::uuid AND id<>$2::uuid", f.company, f.task.ID)
		if err != nil {
			t.Error("rich posting fixture cleanup failed")
		}
	})
	claim, err := a.Claim(context.Background(), Simple)
	if err != nil || claim == nil {
		t.Fatalf("rich monitor owned claim failed: %v", err)
	}
	return f, a, claim
}

func TestRealOwnedRichMonitorInsertTouchRelistAndDescriptionDeduplication(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "First title", "<p>First body</p>")
	result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.Inserted != 1 {
		t.Fatalf("native rich insert failed: %+v / %v", result, err)
	}
	var id, company, board, identity, html string
	var active, uploaded bool
	var due, scraped *time.Time
	var titles, locales []string
	var hash int64
	var updated time.Time
	var postingUpdated time.Time
	read := func() {
		t.Helper()
		if err := f.observer.QueryRow(ctx, `SELECT jp.id::text,jp.company_id::text,jp.board_id::text,jp.source_identity,jp.titles,jp.locales,
 jp.is_active,jp.next_scrape_at,jp.last_scraped_at,d.html,d.hash,d.r2_uploaded,d.updated_at,jp.updated_at
 FROM job_posting jp JOIN descriptions d ON d.posting_id=jp.id AND d.locale='en' WHERE jp.source_url=$1`, posting.URL).Scan(
			&id, &company, &board, &identity, &titles, &locales, &active, &due, &scraped, &html, &hash, &uploaded, &updated, &postingUpdated); err != nil {
			t.Fatal(err)
		}
	}
	read()
	if company != f.company || board != f.task.ID || identity != posting.URL || !active || due != nil || scraped != nil || uploaded || html != posting.Content.Description.HTML || hash != posting.Content.Description.Hash || !reflect.DeepEqual(titles, []string{"First title"}) || !reflect.DeepEqual(locales, []string{"en"}) {
		t.Fatal("rich insert changed identity, upload or no-detail scheduling contract")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE descriptions SET r2_uploaded=true WHERE posting_id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	beforeDescription := updated
	beforePosting := postingUpdated
	posting.Content.Fields.Titles = []string{"Refreshed title"}
	result, err = a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.Touched != 1 {
		t.Fatalf("native rich touch failed: %+v / %v", result, err)
	}
	read()
	if !postingUpdated.After(beforePosting) {
		t.Fatal("rich content refresh did not advance the exporter CDC timestamp")
	}
	if !uploaded || !updated.Equal(beforeDescription) || due != nil || scraped != nil || !reflect.DeepEqual(titles, []string{"Refreshed title"}) {
		t.Fatal("equal description lost completed upload or monitor touch became a detail scrape")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,missing_count=3,scrape_failures=3,next_scrape_at=now(),last_seen_at=now()-interval '1 day' WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	posting.Content.Description, err = richDescription("<p>Updated body</p>", "en")
	if err != nil {
		t.Fatal(err)
	}
	result, err = a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.Relisted != 1 {
		t.Fatalf("native rich relist failed: %+v / %v", result, err)
	}
	read()
	var missing, failures int
	if err := f.observer.QueryRow(ctx, "SELECT missing_count,scrape_failures FROM job_posting WHERE id=$1::uuid", id).Scan(&missing, &failures); err != nil {
		t.Fatal(err)
	}
	if !active || due != nil || scraped != nil || uploaded || missing != 0 || failures != 0 || html != "<p>Updated body</p>" {
		t.Fatal("relist did not reset retry/liveness and pending description state")
	}
	// Chunk persistence does not authorize completion or absence detection.
	var fence, originalTitle string
	var boardDue time.Time
	if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&fence); err != nil {
		t.Fatal(err)
	}
	if err := f.observer.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid AND is_active=true", f.task.ID).Scan(&originalTitle); err != nil {
		t.Fatal("partial batch delisted an unseen posting")
	}
	if err := f.observer.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&boardDue); err != nil {
		t.Fatal(err)
	}
	if fence != "active" || originalTitle != "Original" || !boardDue.Before(time.Now()) || claim.RecoveredReceipt() != nil {
		t.Fatal("posting batch advanced board cycle authority")
	}
}

func TestRealOwnedRichMonitorRefreshUsesReplacementAndNullableRetentionRules(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	var url string
	if err := f.observer.QueryRow(ctx, "SELECT source_url FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&url); err != nil {
		t.Fatal(err)
	}
	if _, err := f.observer.Exec(ctx, `UPDATE job_posting SET salary_min=100, salary_max=200, salary_currency='USD',salary_period='yearly',salary_eur=100,
 location_ids=ARRAY[123],location_types=ARRAY['onsite'],employment_type='full_time',experience_min=2,experience_max=5,
 technology_ids=ARRAY[123],scrape_failures=3,next_scrape_at=NULL WHERE id=$1::uuid`, f.task.ID); err != nil {
		t.Fatal(err)
	}
	posting := richPosting(t, url, "", "")
	posting.Content.Fields.Titles = []string{}
	result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.Touched != 1 {
		t.Fatal(err)
	}
	var salary *int64
	var employment *string
	var locations, technologies []int64
	var titles []string
	var min, max *float64
	var failures int
	if err := f.observer.QueryRow(ctx, `SELECT salary_min,employment_type,location_ids,technology_ids,titles,experience_min,experience_max,scrape_failures
 FROM job_posting WHERE id=$1::uuid`, f.task.ID).Scan(&salary, &employment, &locations, &technologies, &titles, &min, &max, &failures); err != nil {
		t.Fatal(err)
	}
	if salary != nil || employment != nil || locations != nil || len(titles) != 0 || min == nil || max == nil || *min != 2 || *max != 5 || !reflect.DeepEqual(technologies, []int64{123}) || failures != 3 {
		t.Fatal("rich refresh retained cleared fields or erased absent derivations/detail retry budget")
	}
	x := 3.0
	posting.Content.Fields.ExperienceMin = &x
	if _, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting}); err != nil {
		t.Fatal(err)
	}
	if err := f.observer.QueryRow(ctx, "SELECT experience_min,experience_max FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&min, &max); err != nil || min == nil || *min != 3 || max != nil {
		t.Fatal("new experience range did not replace both bounds")
	}
}

func TestRealOwnedRichMonitorForeignLivenessAndRelistPreserveFirstOwner(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	foreignBoard, foreignPosting := ordinaryID(t), ordinaryID(t)
	url := "https://job-boards.greenhouse.io/fixture/jobs/" + foreignPosting
	if _, err := f.observer.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url) VALUES($1::uuid,$2::uuid,$3,$4)", foreignBoard, f.company, "foreign-"+foreignBoard, "https://fixture.invalid/"+foreignBoard); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE board_id=$1::uuid", foreignBoard)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", foreignBoard)
	})
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,missing_count,last_seen_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,ARRAY['Foreign original'],ARRAY['fr'],2,now()-interval '1 day')`, foreignPosting, f.company, foreignBoard, url); err != nil {
		t.Fatal(err)
	}
	posting := richPosting(t, url, "Discovering board title", "<p>Discovered body</p>")
	result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.Foreign != 1 {
		t.Fatalf("foreign liveness failed: %+v / %v", result, err)
	}
	var board, title string
	var missing, descriptions int
	if err := f.observer.QueryRow(ctx, "SELECT board_id::text,titles[1],missing_count,(SELECT count(*) FROM descriptions WHERE posting_id=jp.id) FROM job_posting jp WHERE id=$1::uuid", foreignPosting).Scan(&board, &title, &missing, &descriptions); err != nil {
		t.Fatal(err)
	}
	if board != foreignBoard || title != "Foreign original" || missing != 0 || descriptions != 0 {
		t.Fatal("active foreign discovery stole content or attribution")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,scrape_failures=3 WHERE id=$1::uuid", foreignPosting); err != nil {
		t.Fatal(err)
	}
	result, err = a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
	if err != nil || result == nil || result.ForeignRelisted != 1 {
		t.Fatalf("foreign relist failed: %+v / %v", result, err)
	}
	var active bool
	var due *time.Time
	if err := f.observer.QueryRow(ctx, "SELECT board_id::text,titles[1],is_active,next_scrape_at FROM job_posting WHERE id=$1::uuid", foreignPosting).Scan(&board, &title, &active, &due); err != nil {
		t.Fatal(err)
	}
	if board != foreignBoard || title != "Discovering board title" || !active || due != nil {
		t.Fatal("foreign relist lost owner or rich no-detail behavior")
	}
}

func TestRealOwnedRichMonitorAtomicRollbackAndStaleCanonicalRejection(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	good := richPosting(t, "https://fixture.invalid/jobs/"+ordinaryID(t), "Valid", "<p>Valid body</p>")
	bad := richPosting(t, "https://fixture.invalid/jobs/"+ordinaryID(t), "Bad\x00title", "<p>Bad body</p>")
	before := snapshot(t, f.client)
	beforeLease, err := f.client.redis.ZScore(ctx, "inflight:simple", inflight(&claim.task)).Result()
	if err != nil {
		t.Fatal(err)
	}
	if result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{good, bad}); result != nil || err == nil {
		t.Fatal("database failure became successful partial chunk")
	}
	assertAbsent := func() {
		t.Helper()
		var count int
		if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE source_url=ANY($1::text[])", []string{good.URL, bad.URL}).Scan(&count); err != nil || count != 0 {
			t.Fatal("rolled back or rejected chunk escaped into canonical postings")
		}
	}
	assertAbsent()
	// Detached content must carry the hash of the exact staged UTF-8 bytes.
	good.Content.Description.Hash++
	if result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{good}); result != nil || !errors.Is(err, ErrConfiguration) {
		t.Fatal("changed staged hash was persisted")
	}
	good.Content.Description.Hash--
	assertAbsent()
	after := snapshot(t, f.client)
	// The authoritative write renews its live lease before entering SQL; a
	// rolled-back posting callback may retain that monotonic renewal only.
	delete(before, "inflight:simple")
	delete(after, "inflight:simple")
	afterLease, err := f.client.redis.ZScore(ctx, "inflight:simple", inflight(&claim.task)).Result()
	if err != nil || afterLease < beforeLease || !reflect.DeepEqual(before, after) {
		t.Fatal("failed chunk changed Redis claim/config/schedule")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET is_enabled=false WHERE id=$1::uuid", f.task.ID); err != nil {
		t.Fatal(err)
	}
	if result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{good}); result != nil || !errors.Is(err, ErrUnsupportedProfile) {
		t.Fatalf("stale board retained rich write authority: %v", err)
	}
	assertAbsent()
}

func TestRealOwnedRichMonitorRejectsInvalidOrCanceledBatchBeforeEffects(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	posting := richPosting(t, "https://fixture.invalid/jobs/"+ordinaryID(t), "Valid", "<p>Valid body</p>")
	before := snapshot(t, f.client)
	for _, batch := range [][]GreenhouseRichPosting{nil, {posting, posting}, {{URL: posting.URL}}, make([]GreenhouseRichPosting, 501)} {
		if result, err := a.WriteGreenhouseRichBatch(ctx, claim, batch); result != nil || !errors.Is(err, ErrConfiguration) {
			t.Fatal("invalid posting batch accepted")
		}
	}
	canceled, cancel := context.WithCancel(ctx)
	cancel()
	if result, err := a.WriteGreenhouseRichBatch(canceled, claim, []GreenhouseRichPosting{posting}); result != nil || !errors.Is(err, context.Canceled) {
		t.Fatal("canceled batch committed")
	}
	if !reflect.DeepEqual(before, snapshot(t, f.client)) {
		t.Fatal("invalid or canceled batch changed Redis")
	}
	if _, err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Result(); err != nil {
		t.Fatal(err)
	}
	if result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting}); result != nil || !errors.Is(err, ErrAuthorityLost) {
		t.Fatal("changed Redis snapshot retained rich write authority")
	}
}

func TestRealOwnedRichMonitorBoundsDatabaseDeadlockRetries(t *testing.T) {
	for _, recover := range []bool{true, false} {
		t.Run(fmt.Sprintf("recover=%v", recover), func(t *testing.T) {
			f, a, claim := richClaimFixture(t)
			ctx := context.Background()
			posting := richPosting(t, "https://fixture.invalid/jobs/"+ordinaryID(t), "Retry title", "<p>Retry body</p>")
			name := "rich_retry_" + strings.ReplaceAll(ordinaryID(t), "-", "")
			schema := pgx.Identifier{name}.Sanitize()
			sequence := pgx.Identifier{name, "attempts"}.Sanitize()
			function := pgx.Identifier{name, "inject"}.Sanitize()
			trigger := pgx.Identifier{name}.Sanitize()
			if _, err := f.observer.Exec(ctx, "CREATE SCHEMA "+schema); err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() {
				_, _ = f.observer.Exec(context.Background(), "DROP TRIGGER "+trigger+" ON job_posting")
				_, _ = f.observer.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE")
			})
			var quotedURL, quotedSequence string
			if err := f.observer.QueryRow(ctx, "SELECT quote_literal($1::text),quote_literal($2::text)", posting.URL, sequence).Scan(&quotedURL, &quotedSequence); err != nil {
				t.Fatal(err)
			}
			limit := 3
			if !recover {
				limit = 100
			}
			// A private trigger supplies a real PostgreSQL transaction-aborting
			// 40P01. Its nontransactional sequence measures the retry count while
			// each failed posting/description transaction is fully rolled back.
			sql := fmt.Sprintf(`CREATE SEQUENCE %s;
CREATE FUNCTION %s() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.source_url=%s AND nextval(%s)<%d THEN
 RAISE EXCEPTION USING ERRCODE='40P01',MESSAGE='private rich deadlock fixture';
END IF; RETURN NEW; END $$;
CREATE TRIGGER %s BEFORE INSERT ON job_posting FOR EACH ROW EXECUTE FUNCTION %s()`,
				sequence, function, quotedURL, quotedSequence, limit, trigger, function)
			if _, err := f.observer.Exec(ctx, sql); err != nil {
				t.Fatal(err)
			}
			result, err := a.WriteGreenhouseRichBatch(ctx, claim, []GreenhouseRichPosting{posting})
			var attempts, count int
			if err := f.observer.QueryRow(ctx, "SELECT last_value FROM "+sequence).Scan(&attempts); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE source_url=$1", posting.URL).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if attempts != 3 {
				t.Fatal("database deadlock retry budget changed")
			}
			if recover {
				if err != nil || result == nil || result.Inserted != 1 || count != 1 {
					t.Fatalf("rolled-back deadlock attempts leaked counters or blocked recovery: %+v / %v", result, err)
				}
			} else {
				var failure *pgconn.PgError
				if result != nil || !errors.As(err, &failure) || failure.Code != "40P01" || count != 0 {
					t.Fatal("exhausted database retry became success or retained a posting")
				}
			}
		})
	}
}
