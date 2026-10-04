package worker

import (
	"context"
	"errors"
	"fmt"
	"testing"
	"time"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
)

// This private fixture exercises the existing unselected attempt authority.
// It grants no production detail selection; monitor ownership remains separate.
func workdayDetailPersistenceFixture(t *testing.T) (nativePipelineFixture, *queue.Authority, *queue.Claim, *queue.CurrentWorkdayDetail) {
	return workdayDetailFixture(t, false)
}

func workdayDetailFixture(t *testing.T, owned bool) (nativePipelineFixture, *queue.Authority, *queue.Claim, *queue.CurrentWorkdayDetail) {
	t.Helper()
	f := privateRichPipelineFixture(t, "workday", `{"all_sites":false,"scraper_type":"workday"}`)
	ctx := context.Background()
	var epoch int64
	if err := f.pg.QueryRow(ctx, `UPDATE ordinary_worker_ownership_plan SET state='retired'
 WHERE plan_sha256=(SELECT plan_sha256 FROM ordinary_worker_ownership_plan WHERE state='active') RETURNING routing_epoch`).Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	if err := f.r.Del(ctx, "ordinary:ownership:active", "monitors_simple:workday", "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	const source = "https://fixture.wd1.myworkdayjobs.com/Careers/job/City/Engineer_R100"
	if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET source_url=$2,next_scrape_at=now()-interval '1 minute' WHERE id=$1::uuid", f.original, source); err != nil {
		t.Fatal(err)
	}
	if _, err := f.client.EnqueueURLDetail(ctx, queue.URLOnlyDetail{ID: f.original, BoardID: f.board, URL: source, Due: time.Now().Add(-time.Minute)}); err != nil {
		t.Fatal(err)
	}
	a, err := queue.OpenAuthority(ctx, f.dsn, f.client, epoch)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(a.Close)
	if owned {
		plan, err := a.StageOwnership(ctx, ordinaryFixtureSourceRevision(t), []string{f.board}, []string{f.board})
		if err != nil {
			t.Fatal(err)
		}
		if _, err := f.pg.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1", plan.SHA256()); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() {
			_, _ = f.pg.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
		})
		if err := f.r.Set(ctx, "ordinary:ownership:active", plan.ProjectionJSON(), 0).Err(); err != nil {
			t.Fatal(err)
		}
		a, err = queue.OpenOwnedAuthority(ctx, f.dsn, f.client, epoch, plan.SHA256(), plan.SourceRevision())
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(a.Close)
	}
	claim, err := a.Claim(ctx, queue.Simple)
	if err != nil || claim == nil {
		t.Fatal("canonical detail claim unavailable", err)
	}
	detail, err := a.ReadWorkdayDetail(ctx, claim)
	if err != nil || detail == nil || detail.PostingID() != f.original || detail.Profile().BoardID != f.board || detail.PostingID() == detail.Profile().BoardID {
		t.Fatal("posting identity or canonical Workday observation unavailable", err)
	}
	return f, a, claim, detail
}

func TestRealWorkdayDetailSharedEnrichmentDescriptionAndDeadline(t *testing.T) {
	f, a, claim, detail := workdayDetailPersistenceFixture(t)
	ctx := context.Background()
	processor := richPipelinePreparer(t, f).Processor
	title, html, employment, remote := "Senior Software Engineer", "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>", "Full time", "remote"
	content := workday.DetailContent{Title: &title, Description: &html, Locations: []string{"Zurich"}, EmploymentType: &employment, JobLocationType: &remote}
	receipt, err := PersistWorkdayDetail(ctx, a, detail, processor, content)
	if err != nil || receipt == nil || receipt.NextDue() == nil {
		t.Fatal("native detail content and terminal schedule were not committed", err)
	}
	var storedTitle, storedEmployment, currency, storedHTML string
	var locations, technologies []int32
	var locationTypes, locales []string
	var hash *int64
	var descriptionHash int64
	var uploaded, active bool
	var due time.Time
	var failures int
	if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.employment_type,p.salary_currency,p.location_ids,p.technology_ids,
 p.location_types,p.locales,p.description_r2_hash,d.hash,d.html,d.r2_uploaded,p.is_active,p.next_scrape_at,p.scrape_failures
 FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(
		&storedTitle, &storedEmployment, &currency, &locations, &technologies, &locationTypes, &locales,
		&hash, &descriptionHash, &storedHTML, &uploaded, &active, &due, &failures); err != nil {
		t.Fatal(err)
	}
	// The description hash is staged first; the drain publishes the canonical
	// uploaded hash later. A newly fetched posting must retain NULL until then.
	if storedTitle != title || storedEmployment != "full_time" || currency != "CHF" || fmt.Sprint(locations) != "[2]" || fmt.Sprint(technologies) != "[4]" || fmt.Sprint(locationTypes) != "[remote]" || fmt.Sprint(locales) != "[en]" || storedHTML != html || descriptionHash != 2326914150316601214 || hash != nil || uploaded || !active || failures != 0 || !due.Equal(*receipt.NextDue()) || !due.After(time.Now()) {
		t.Fatal("native detail fields, pending description, visibility or canonical deadline differ")
	}
	if err := a.Settle(ctx, claim, receipt); err != nil {
		t.Fatal(err)
	}
	if score, err := f.r.ZScore(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("detail queue did not conserve the committed canonical deadline", err)
	}
}

func TestRealWorkdayDetailEmptyAndFreshReservationPreserveCanonicalContent(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		t.Run(fmt.Sprint(reserved), func(t *testing.T) {
			f, a, _, detail := workdayDetailPersistenceFixture(t)
			ctx := context.Background()
			processor := richPipelinePreparer(t, f).Processor
			content := workday.DetailContent{}
			want := executor.ErrEmptyResult
			if reserved {
				title, html := "New title", "<p>Build software.</p>"
				content.Title, content.Description = &title, &html
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
				want = queue.ErrPublisherReserved
			}
			receipt, err := PersistWorkdayDetail(ctx, a, detail, processor, content)
			if receipt != nil || !errors.Is(err, want) {
				t.Fatal("empty or newly reserved detail committed", err)
			}
			var title string
			var descriptions, failures int
			var active bool
			if err := f.pg.QueryRow(ctx, `SELECT titles[1],is_active,scrape_failures,
 (SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &active, &failures, &descriptions); err != nil || title != "Original" || !active || failures != 0 || descriptions != 0 {
				t.Fatal("refused detail changed visibility/content/failure budget", err)
			}
		})
	}
}
