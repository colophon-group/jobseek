package queue

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"
)

func TestRealOwnedURLOnlyMonitorInsertRepairRetryBudgetAndRelist(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	cycle, err := a.BeginGreenhouseCycle(ctx, claim)
	if err != nil {
		t.Fatal(err)
	}
	raw := "https://fixture.invalid/jobs/" + ordinaryID(t)
	r, err := cycle.WriteURLOnlyBatch(ctx, []string{raw})
	if err != nil || r.Inserted != 1 || len(r.Details) != 1 {
		t.Fatalf("URL stub and detail intent: %+v / %v", r, err)
	}
	id := r.Details[0].ID
	if added, err := f.client.EnqueueURLDetail(ctx, r.Details[0]); err != nil || !added {
		t.Fatal("committed URL stub did not enter separate detail queue", err)
	}
	// Simulate losing that enqueue, without changing canonical detail state.
	if err := f.client.redis.Del(ctx, "ft_scrapes_simple:fixture.invalid", "scrape:"+id).Err(); err != nil {
		t.Fatal(err)
	}
	var board, company string
	var titles, locales []string
	var due *time.Time
	var active bool
	var missing, failures, descriptions int
	read := func() {
		t.Helper()
		if err := f.observer.QueryRow(ctx, `SELECT board_id::text,company_id::text,titles,locales,next_scrape_at,is_active,missing_count,scrape_failures,
 (SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, id).Scan(&board, &company, &titles, &locales, &due, &active, &missing, &failures, &descriptions); err != nil {
			t.Fatal(err)
		}
	}
	read()
	if board != f.task.ID || company != f.company || len(titles) != 0 || len(locales) != 0 || due == nil || !active || descriptions != 0 || r.Details[0].DescriptionHash != nil {
		t.Fatal("URL-only discovery wrote detail data or lost scheduling")
	}
	r, err = cycle.WriteURLOnlyBatch(ctx, []string{raw})
	if err != nil || r.Touched != 1 || len(r.Details) != 1 {
		t.Fatalf("lost initial enqueue not repaired: %+v / %v", r, err)
	}
	if added, err := f.client.EnqueueURLDetail(ctx, r.Details[0]); err != nil || !added {
		t.Fatal("missing-content repair did not restore detail queue/config", err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL,scrape_failures=3 WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	r, err = cycle.WriteURLOnlyBatch(ctx, []string{raw})
	if err != nil || len(r.Details) != 0 {
		t.Fatal("monitor touch retried a terminal detail tombstone", err)
	}
	read()
	if due != nil || failures != 3 {
		t.Fatal("monitor touch reset exhausted detail retry budget")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,missing_count=4 WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	r, err = cycle.WriteURLOnlyBatch(ctx, []string{raw})
	if err != nil || r.Relisted != 1 || len(r.Details) != 1 {
		t.Fatal("true relist did not issue fresh detail intent", err)
	}
	read()
	if !active || due == nil || failures != 0 || missing != 0 {
		t.Fatal("true relist did not reset liveness and retry state")
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET titles=ARRAY['Existing title'],locales=ARRAY['en'],description_r2_hash=123,next_scrape_at=now()+interval '1 day' WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	r, err = cycle.WriteURLOnlyBatch(ctx, []string{raw})
	if err != nil || len(r.Details) != 0 {
		t.Fatal("healthy detail was rescheduled by inventory touch", err)
	}
	read()
	if !reflect.DeepEqual(titles, []string{"Existing title"}) || !reflect.DeepEqual(locales, []string{"en"}) {
		t.Fatal("inventory touch replaced canonical detail")
	}
	var fence string
	var boardDue time.Time
	if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&fence); err != nil {
		t.Fatal(err)
	}
	if err := f.observer.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&boardDue); err != nil {
		t.Fatal(err)
	}
	if fence != "active" || !boardDue.Before(time.Now()) {
		t.Fatal("posting chunk prematurely completed monitor")
	}
}

func TestRealOwnedURLOnlyForeignRelistPreservesCanonicalDetailRouting(t *testing.T) {
	f, a, claim := richClaimFixture(t)
	ctx := context.Background()
	board, id := ordinaryID(t), ordinaryID(t)
	raw := "https://fixture.invalid/jobs/" + id
	if _, err := f.observer.Exec(ctx, "INSERT INTO job_board(id,company_id,board_slug,board_url,scraper_needs_browser) VALUES($1::uuid,$2::uuid,$3,$4,true)", board, f.company, "foreign-"+board, "https://fixture.invalid/"+board); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE board_id=$1::uuid", board)
		_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_board WHERE id=$1::uuid", board)
	})
	if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,titles,locales,description_r2_hash,last_seen_at)
 VALUES($1::uuid,$2::uuid,$3::uuid,$4,ARRAY['Original'],ARRAY['fr'],987,now()-interval '1 day')`, id, f.company, board, raw); err != nil {
		t.Fatal(err)
	}
	r, err := a.WriteURLOnlyBatch(ctx, claim, []string{raw})
	if err != nil || r.Foreign != 1 || len(r.Details) != 0 {
		t.Fatal("active foreign discovery changed detail routing", err)
	}
	if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET is_active=false,scrape_failures=3 WHERE id=$1::uuid", id); err != nil {
		t.Fatal(err)
	}
	r, err = a.WriteURLOnlyBatch(ctx, claim, []string{raw})
	if err != nil || r.ForeignRelisted != 1 || len(r.Details) != 1 {
		t.Fatal("foreign relist not scheduled", err)
	}
	d := r.Details[0]
	if d.BoardID != board || d.ID != id || !d.Browser || d.DescriptionHash == nil || *d.DescriptionHash != 987 {
		t.Fatal("foreign relist transferred detail routing or content hash")
	}
	var originalBoard, title string
	if err := f.observer.QueryRow(ctx, "SELECT board_id::text,titles[1] FROM job_posting WHERE id=$1::uuid", id).Scan(&originalBoard, &title); err != nil {
		t.Fatal(err)
	}
	if originalBoard != board || title != "Original" {
		t.Fatal("foreign inventory transferred owner or content")
	}
}

func TestRealOwnedURLOnlyPublisherAndConfigFenceBeforeEffects(t *testing.T) {
	for _, publisher := range []bool{true, false} {
		t.Run(map[bool]string{true: "publisher", false: "cache_changed"}[publisher], func(t *testing.T) {
			f, a, claim := richClaimFixture(t)
			ctx := context.Background()
			raw := "https://fixture.invalid/jobs/" + ordinaryID(t)
			if publisher {
				if _, err := f.observer.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.task.ID); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "metadata", `{"token":"changed","scraper_type":"skip"}`).Err(); err != nil {
					t.Fatal(err)
				}
			}
			r, err := a.WriteURLOnlyBatch(ctx, claim, []string{raw})
			want := ErrAuthorityLost
			if publisher {
				want = ErrPublisherReserved
			}
			if r != nil || !errors.Is(err, want) {
				t.Fatalf("refusal: %+v / %v", r, err)
			}
			var n int
			if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE source_url=$1", raw).Scan(&n); err != nil || n != 0 {
				t.Fatal("refused monitor inserted URL")
			}
		})
	}
}
