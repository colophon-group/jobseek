package worker

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func workdayTLSFixtureClient(t *testing.T, handler http.HandlerFunc) *VerifiedDirectHTTP {
	t.Helper()
	client := explicitTLSFixtureClient(t, handler, true, true)
	transport := client.client.Transport.(*directTransport)
	transport.allowed["fixture.wd1.myworkdayjobs.com"] = true
	dial := transport.dial
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "fixture.wd1.myworkdayjobs.com:443" {
			return nil, ErrUnsafeURL
		}
		return dial(ctx, network, "example.com:443")
	}
	return client
}

func TestRealWorkdayMonitorExplicitTLSExceptionCanonicalSettlement(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		t.Run(fmt.Sprint(reserved), func(t *testing.T) {
			f := privateRichPipelineFixture(t, "workday", `{"all_sites":false,"scraper_type":"workday","ssl_verify":false}`)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			calls := 0
			client := workdayTLSFixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Method != "POST" || r.ProtoMajor != 2 || r.URL.Path != "/wday/cxs/fixture/Careers/jobs" {
					t.Error("CXS origin/protocol changed")
				}
				if reserved {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
				}
				fmt.Fprint(w, `{"total":1,"jobPostings":[{"externalPath":"/job/New_R200"}]}`)
			})
			if skip, e := runtimeClaimSkipsSSL(ctx, f.a, claim); e != nil || !skip {
				t.Fatal("bound monitor client not selected", e)
			}
			wrong := *client
			wrong.skipSSL = false
			preparer := richPipelinePreparer(t, f)
			if result, e := RunGreenhouseClaim(ctx, f.a, claim, &wrong, preparer, circuits); e == nil || result.Settled || calls != 0 {
				t.Fatal("wrong monitor transport reached effects")
			}
			result, e := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
			if e != nil || !result.Settled || calls != 1 {
				t.Fatal("explicit CXS inventory failed", e, calls)
			}
			assertRichDeadlineAndLease(t, f, "workday")
			var inserted, missing int
			var optOut bool
			if e := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &optOut, &inserted); e != nil {
				t.Fatal(e)
			}
			want := 1
			if reserved {
				want = 0
			}
			if missing != want || inserted != want || optOut != reserved {
				t.Fatal("explicit TLS inventory effects changed")
			}
			if f.r.ZCard(ctx, "ft_scrapes_simple:fixture.wd1.myworkdayjobs.com").Val() != int64(want) {
				t.Fatal("independent details not conserved")
			}
		})
	}
}

func TestRealNativeExecutableSelectsWorkdayExplicitTLSException(t *testing.T) {
	f := privateRichPipelineFixture(t, "workday", `{"all_sites":false,"scraper_type":"workday","ssl_verify":false}`)
	ctx := context.Background()
	if _, e := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); e != nil {
		t.Fatal(e)
	}
	executable := newNativeExecutableFixture(t, f, privatePipelineReferenceDSN(t, f))
	process := executable.start(t, "explicit-workday-tls")
	waitNativeFixture(t, process, "canonical monitor client and publisher settlement", func() bool {
		var state string
		e := f.pg.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.board).Scan(&state)
		return e == nil && state == "completed" && f.r.ZCard(ctx, "inflight:simple").Val() == 0
	})
	if e := process.command.Process.Signal(os.Interrupt); e != nil {
		t.Fatal(e)
	}
	if e := process.wait(t); e != nil {
		t.Fatal("native executable did not drain", e)
	}
}

func TestRealWorkdayDetailExplicitTLSExceptionSettlementAndSelection(t *testing.T) {
	for _, reserved := range []bool{false, true} {
		t.Run(fmt.Sprint(reserved), func(t *testing.T) {
			f, authority, claim, _ := workdayDetailFixture(t, true, `{"all_sites":false,"scraper_type":"workday","ssl_verify":false}`)
			ctx := context.Background()
			calls := 0
			client := explicitTLSFixtureClient(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if r.Host != "fixture.wd1.myworkdayjobs.com" || r.ProtoMajor != 2 || r.URL.Path != "/wday/cxs/fixture/Careers/job/City/Engineer_R100" {
					t.Error("detail origin or HTTP/2 changed")
				}
				if reserved {
					w.Header().Set("TDM-Reservation", "1")
					w.WriteHeader(503)
					return
				}
				fmt.Fprint(w, `{"jobPostingInfo":{"title":"TLS Engineer","jobDescription":"<p>Build reliable services.</p>","location":"Zurich"}}`)
			}, true, true)
			transport := client.client.Transport.(*directTransport)
			transport.allowed["fixture.wd1.myworkdayjobs.com"] = true
			dial := transport.dial
			transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
				if address != "fixture.wd1.myworkdayjobs.com:443" {
					return nil, ErrUnsafeURL
				}
				return dial(ctx, network, "example.com:443")
			}
			if skip, err := runtimeClaimSkipsSSL(ctx, authority, claim); err != nil || !skip {
				t.Fatal("canonical detail setting did not select sealed client", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			wrong := *client
			wrong.skipSSL = false
			processor := richPipelinePreparer(t, f).Processor
			if result, err := RunDetail(ctx, authority, claim, &wrong, processor, circuits); err == nil || result.Settled || calls != 0 {
				t.Fatal("wrong transport reached origin or settlement")
			}
			result, err := RunDetail(ctx, authority, claim, client, processor, circuits)
			if err != nil || !result.Settled || calls != 1 {
				t.Fatal("explicit detail exception did not settle", err, calls)
			}
			var title string
			var due time.Time
			var leased *time.Time
			var optOut bool
			var descriptions int
			if err := f.pg.QueryRow(ctx, `SELECT titles[1],next_scrape_at,leased_until,tdm_reserved,(SELECT count(*) FROM descriptions WHERE posting_id=p.id) FROM job_posting p WHERE id=$1::uuid`, f.original).Scan(&title, &due, &leased, &optOut, &descriptions); err != nil {
				t.Fatal(err)
			}
			if reserved {
				if title != "Original" || descriptions != 0 || !optOut || result.Cycle.Status != "publisher_reserved" {
					t.Fatal("publisher policy lost precedence over 503 or content")
				}
			} else {
				var source string
				if err := f.pg.QueryRow(ctx, "SELECT html FROM descriptions WHERE posting_id=$1::uuid", f.original).Scan(&source); err != nil || !strings.Contains(source, "reliable services") || title != "TLS Engineer" || descriptions != 1 {
					t.Fatal("original detail content not persisted", err)
				}
			}
			if leased != nil || !due.After(time.Now()) || f.r.ZScore(ctx, "scrapes_simple:fixture.wd1.myworkdayjobs.com", f.original).Val() != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
				t.Fatal("detail schedule or lease not conserved")
			}
		})
	}
}
