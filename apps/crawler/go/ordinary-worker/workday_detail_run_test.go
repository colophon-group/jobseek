package worker

import (
	"context"
	"errors"
	"fmt"
	"github.com/redis/go-redis/v9"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type workdayDetailRoundTrip func(*http.Request) (*http.Response, error)

func (f workdayDetailRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestRealWorkdayDetailOwnedRunnerFetchesEnrichesAndSettles(t *testing.T) {
	f, a, claim, _ := workdayDetailFixture(t, true)
	ctx := context.Background()
	calls := 0
	client := &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "GET" || r.URL.String() != "https://fixture.wd1.myworkdayjobs.com/wday/cxs/fixture/Careers/job/City/Engineer_R100" {
			t.Fatal("native detail used wrong origin request")
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/json"}}, Body: io.NopCloser(strings.NewReader(`{"jobPostingInfo":{"title":"Senior Software Engineer","jobDescription":"<p>Python. Salary CHF 100000-120000 yearly.</p>","location":"Zurich","timeType":"Full time"}}`)), Request: r}, nil
	})}}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunWorkdayDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || !result.Settled || result.Cycle.Status != "succeeded" || calls != 1 || result.TaskKind != queue.Scrape {
		t.Fatal("native detail runner failed", result, err)
	}
	var title, html string
	var locations []int32
	var active bool
	var due time.Time
	if err := f.pg.QueryRow(ctx, `SELECT p.titles[1],p.location_ids,p.is_active,p.next_scrape_at,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.id=$1::uuid`, f.original).Scan(&title, &locations, &active, &due, &html); err != nil {
		t.Fatal(err)
	}
	if title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || !active || !due.After(time.Now()) || !strings.Contains(html, "Salary CHF") {
		t.Fatal("actual fetched detail did not enrich/persist")
	}
	if score, err := f.r.ZScore(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("detail deadline or lease not conserved", err)
	}
}

func TestRealWorkdayDetailRunnerPreservesFailureAndPublisherPolicy(t *testing.T) {
	for _, mode := range []string{"404-empty", "s22-empty", "410-gone", "400-budget", "503-transient", "empty-content", "transport", "invalid-payload", "header-reserved", "existing-reserved", "fresh-reserved", "inactive", "inactive-header-reserved", "host-circuit"} {
		t.Run(mode, func(t *testing.T) {
			f, a, claim, _ := workdayDetailFixture(t, true)
			ctx := context.Background()
			if mode == "existing-reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "inactive" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "host-circuit" {
				if err := f.r.Set(ctx, "host_open:fixture.wd1.myworkdayjobs.com", fmt.Sprint(float64(time.Now().Add(time.Hour).Unix())), time.Hour).Err(); err != nil {
					t.Fatal(err)
				}
			}
			if err := f.r.Set(ctx, "host_fail:fixture.wd1.myworkdayjobs.com", "1", time.Hour).Err(); err != nil {
				t.Fatal(err)
			}
			calls := 0
			client := &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
				calls++
				status, body := 200, `{"jobPostingInfo":{"title":"Native","jobDescription":"<p>Build software.</p>"}}`
				header := http.Header{"Content-Type": {"application/json"}}
				switch mode {
				case "transport":
					return nil, errors.New("fixture transport failure")
				case "invalid-payload":
					body = `<html>invalid Workday response</html>`
				case "404-empty":
					status, body = 404, `{}`
				case "s22-empty":
					status, body = 403, `{"errorCode":"S22"}`
				case "410-gone":
					status, body = 410, `{}`
				case "400-budget":
					status, body = 400, `{}`
				case "503-transient":
					status, body = 503, `{}`
				case "empty-content":
					body = `{"jobPostingInfo":{}}`
				case "header-reserved":
					status = 410
					header.Set("TDM-Reservation", "1")
				case "inactive-header-reserved":
					status = 410
					header.Set("TDM-Reservation", "1")
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET is_active=false WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				case "fresh-reserved":
					if _, err := f.pg.Exec(ctx, "UPDATE job_posting SET tdm_reserved=true WHERE id=$1::uuid", f.original); err != nil {
						t.Fatal(err)
					}
				}
				return &http.Response{StatusCode: status, Header: header, Body: io.NopCloser(strings.NewReader(body)), Request: r}, nil
			})}}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			result, err := RunWorkdayDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("policy/failure detail did not settle", err)
			}
			streak := f.r.Get(ctx, "host_fail:fixture.wd1.myworkdayjobs.com").Val()
			expectedStreak := ""
			switch mode {
			case "s22-empty", "503-transient", "transport", "invalid-payload":
				expectedStreak = "2"
			case "existing-reserved", "inactive", "host-circuit":
				expectedStreak = "1"
			}
			if streak != expectedStreak {
				t.Fatalf("scrape host reachability changed: got %q, want %q", streak, expectedStreak)
			}
			if mode == "invalid-payload" && calls != 3 {
				t.Fatal("provider-invalid payload did not exhaust its bounded retry")
			}
			var active, reserved bool
			var title string
			var due *time.Time
			var descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT p.is_active,p.tdm_reserved,p.titles[1],p.next_scrape_at,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE p.id=$1::uuid`, f.original).Scan(&active, &reserved, &title, &due, &descriptions); err != nil {
				t.Fatal(err)
			}
			if title != "Original" || descriptions != 0 {
				t.Fatal("failed/reserved detail rewrote content")
			}
			if active != (mode != "410-gone" && mode != "inactive" && mode != "inactive-header-reserved") {
				t.Fatal("detail failure changed visibility incorrectly", mode)
			}
			if mode == "header-reserved" || mode == "existing-reserved" || mode == "fresh-reserved" {
				if !reserved || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("reservation lost")
				}
			}
			if mode == "inactive-header-reserved" && (!reserved || result.Cycle.Status != "unscheduled") {
				t.Fatal("inactive race discarded positive opt-out")
			}
			if mode == "existing-reserved" || mode == "inactive" || mode == "host-circuit" {
				if calls != 0 {
					t.Fatal("inert/reserved/circuit-open detail reached origin")
				}
			}
			if mode == "inactive" || mode == "410-gone" || mode == "inactive-header-reserved" {
				if due != nil {
					t.Fatal("unscheduled detail retained deadline")
				}
			} else if due == nil || !due.After(time.Now()) {
				t.Fatal("terminal detail was hot-looped")
			}
			if f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("terminal detail retained lease")
			}
		})
	}
}

func TestRealWorkdayDetailRunnerRecoversCommitWithoutRepeatingOrigin(t *testing.T) {
	f, a, claim, _ := workdayDetailFixture(t, true)
	ctx := context.Background()
	calls := 0
	client := &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		calls++
		if err := f.r.Set(ctx, "ready:simple:1", "private settlement fault", 0).Err(); err != nil {
			t.Fatal(err)
		}
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": {"application/json"}}, Body: io.NopCloser(strings.NewReader(`{"jobPostingInfo":{"title":"Native","jobDescription":"<p>Build software.</p>"}}`)), Request: r}, nil
	})}}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunWorkdayDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if !errors.Is(err, queue.ErrObservation) || result.Settled || result.Cycle == nil || result.Cycle.Receipt == nil || calls != 1 {
		t.Fatal("settlement fault lost committed detail", err)
	}
	var due time.Time
	if err := f.pg.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", f.original).Scan(&due); err != nil {
		t.Fatal(err)
	}
	var epoch int64
	var plan, source string
	if err := f.pg.QueryRow(ctx, "SELECT routing_epoch,plan_sha256,source_revision FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&epoch, &plan, &source); err != nil {
		t.Fatal(err)
	}
	a.Close()
	if err := f.r.Del(ctx, "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	if err := f.r.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: "scrape|fixture.wd1.myworkdayjobs.com|" + f.original}).Err(); err != nil {
		t.Fatal(err)
	}
	body, err := os.ReadFile("../../src/lua/reap_expired.lua")
	if err != nil {
		t.Fatal(err)
	}
	if err := queue.WithOrdinaryLeaseRetirement(ctx, f.pg, func(ctx context.Context) error {
		now, err := f.r.Time(ctx).Result()
		if err != nil {
			return err
		}
		at := float64(now.UnixMicro()) / 1e6
		return f.r.Eval(ctx, string(body), nil, "simple", at, 10, 3, at, "guarded").Err()
	}); err != nil {
		t.Fatal(err)
	}
	replacement, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, epoch, plan, source)
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()
	recovered, err := replacement.Claim(ctx, queue.Simple)
	if err != nil || recovered == nil || recovered.RecoveredReceipt() == nil {
		t.Fatal("detail commit unavailable for recovery", err)
	}
	result, err = RunWorkdayDetail(ctx, replacement, recovered, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || !result.Settled || result.Cycle.Status != "recovered" || calls != 1 || result.HTTP.Requests != 0 {
		t.Fatal("detail recovery repeated origin", err)
	}
	var after time.Time
	if err := f.pg.QueryRow(ctx, "SELECT next_scrape_at FROM job_posting WHERE id=$1::uuid", f.original).Scan(&after); err != nil || !due.Equal(after) {
		t.Fatal("recovery repeated detail persistence")
	}
	if score, err := f.r.ZScore(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", f.original).Result(); err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("recovered detail deadline/lease lost", err)
	}
}

func TestRealWorkdayDetailCancellationPreservesAttemptForRecovery(t *testing.T) {
	f, a, claim, _ := workdayDetailFixture(t, true)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client := &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) { cancel(); return nil, ctx.Err() })}}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunWorkdayDetail(ctx, a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if !errors.Is(err, context.Canceled) || result.Settled {
		t.Fatal("cancelled detail acknowledged or spent failure budget", err)
	}
	var title, state string
	var failures int
	if err := f.pg.QueryRow(context.Background(), `SELECT p.titles[1],p.scrape_failures,f.state FROM job_posting p JOIN ordinary_worker_write_fence f ON f.task_id=p.id AND f.task_kind='scrape' WHERE p.id=$1::uuid`, f.original).Scan(&title, &failures, &state); err != nil || title != "Original" || failures != 0 || state != "active" || f.r.ZCard(context.Background(), "inflight:simple").Val() != 1 {
		t.Fatal("cancelled detail lost retained attempt/content", err)
	}
}

func TestRealWorkdayDetailInvalidPublisherPolicyPreservesOptOutWithoutContentWrite(t *testing.T) {
	f, a, claim, _ := workdayDetailFixture(t, true)
	client := &VerifiedDirectHTTP{client: &http.Client{Transport: workdayDetailRoundTrip(func(r *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 410, Header: http.Header{"Tdm-Reservation": {"1"}, "Tdm-Policy": {strings.Repeat("x", 8193)}}, Body: io.NopCloser(strings.NewReader(`{}`)), Request: r}, nil
	})}}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	result, err := RunWorkdayDetail(context.Background(), a, claim, client, richPipelinePreparer(t, f).Processor, circuits)
	if err != nil || !result.Settled || result.Cycle.Status != "publisher_reserved" || len(result.Diagnostics) != 1 || result.Diagnostics[0] != "invalid_policy_url" {
		t.Fatal("invalid policy discarded positive opt-out", err)
	}
	var title string
	var failures int
	var active, reserved bool
	var policy *string
	if err := f.pg.QueryRow(context.Background(), "SELECT titles[1],scrape_failures,is_active,tdm_reserved,tdm_reservation->>'policy_url' FROM job_posting WHERE id=$1::uuid", f.original).Scan(&title, &failures, &active, &reserved, &policy); err != nil || title != "Original" || failures != 0 || !active || !reserved || policy != nil || f.r.ZCard(context.Background(), "inflight:simple").Val() != 0 {
		t.Fatal("invalid policy changed content/visibility or failure budget", err)
	}
}
