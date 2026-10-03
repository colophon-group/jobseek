package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/redis/go-redis/v9"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func verifiedClaimFixtureClient(t *testing.T, handler http.Handler) *VerifiedDirectHTTP {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	transport.inner.TLSClientConfig.ServerName = "example.com" // fixture SAN; chain/hostname verification stays enabled
	transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
	}
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "8.8.8.8:443" {
			t.Error("claim fetch did not pin public validation")
		}
		return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
	}
	// Only tests inside this package can replace the private physical dial.
	return &VerifiedDirectHTTP{client: client}
}

func claimFixture(t *testing.T, f nativePipelineFixture) (*queue.Claim, *queue.HostCircuits) {
	t.Helper()
	claim, err := f.a.Claim(context.Background(), queue.Simple)
	if err != nil || claim == nil {
		t.Fatal("claim runner fixture missing installed claim", err)
	}
	circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
	if err != nil {
		t.Fatal(err)
	}
	return claim, circuits
}

func assertClaimReceiptSettlement(t *testing.T, f nativePipelineFixture, result *GreenhouseClaimResult) {
	t.Helper()
	if result == nil || !result.Settled || result.Cycle == nil || result.Cycle.Receipt == nil {
		t.Fatal("run has no settled canonical receipt")
	}
	var due time.Time
	if err := f.pg.QueryRow(context.Background(), "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&due); err != nil {
		t.Fatal(err)
	}
	score, err := f.r.ZScore(context.Background(), "monitors_simple:greenhouse", f.board).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || !due.Equal(*result.Cycle.Receipt.NextDue()) || f.r.ZCard(context.Background(), "inflight:simple").Val() != 0 {
		t.Fatal("claim runner lost due/receipt/queue conservation")
	}
}

func TestRealClaimRunnerVerifiedRedirectResourceOutcomes(t *testing.T) {
	for _, mode := range []string{"reserved404", "reserved503", "gone404", "redirect_header404", "status503", "bad_inventory", "partial_reserved404", "blocked_redirect"} {
		t.Run(mode, func(t *testing.T) {
			f := privatePipelineFixture(t)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			finalURL := "https://final.example.com/resource?opaque=1"
			var requests atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				requests.Add(1)
				if r.URL.Path != "/resource" {
					if mode == "redirect_header404" {
						w.Header().Set("TDM-Reservation", "1")
						w.Header().Set("TDM-Policy", "redirect-only")
					}
					location := finalURL
					if mode == "blocked_redirect" {
						location = "https://10.0.0.1/resource"
					}
					w.Header().Set("Location", location)
					w.WriteHeader(302)
					return
				}
				if strings.HasPrefix(mode, "reserved") || mode == "partial_reserved404" {
					w.Header().Set("TDM-Reservation", "1")
					w.Header().Set("TDM-Policy", "  final policy  ")
				}
				status := 404
				if mode == "reserved503" || mode == "status503" {
					status = 503
				}
				if mode == "bad_inventory" {
					status = 200
				}
				if mode == "partial_reserved404" {
					w.Header().Set("Content-Length", "100")
				}
				w.WriteHeader(status)
				_, _ = w.Write([]byte("invalid!"))
			}))
			if err := f.r.Set(ctx, "host_fail:final.example.com", "2", time.Hour).Err(); err != nil {
				t.Fatal(err)
			}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil {
				t.Fatal("verified claim lifecycle failed", err)
			}
			assertClaimReceiptSettlement(t, f, result)
			wantRequests := int64(2)
			if mode == "blocked_redirect" {
				wantRequests = 1
			}
			if requests.Load() != wantRequests || result.HTTP.Requests != wantRequests || result.HTTP.Responses != wantRequests || result.HTTP.NoResponse != 0 {
				t.Fatalf("redirect request accounting differs: %+v", result.HTTP)
			}
			var reserved, active bool
			var failures, gone int
			var resource, lastError *string
			var evidence []byte
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count,last_gone_endpoint,tdm_reservation,last_error FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone, &resource, &evidence, &lastError); err != nil {
				t.Fatal(err)
			}
			if err := f.pg.QueryRow(ctx, "SELECT is_active FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active); err != nil || !active {
				t.Fatal("failure/resource outcome applied inventory absence")
			}
			switch mode {
			case "reserved404", "reserved503":
				if result.Cycle.Status != "publisher_reserved" || !reserved || failures != 0 || gone != 0 || !strings.Contains(string(evidence), finalURL) || !strings.Contains(string(evidence), "final policy") {
					t.Fatal("final publisher signal lost precedence/provenance", string(evidence))
				}
				if f.r.Exists(ctx, "host_fail:final.example.com").Val() != 0 {
					t.Fatal("publisher success did not reset observed host")
				}
				var postingReserved bool
				if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved FROM job_posting WHERE id=$1::uuid", f.original).Scan(&postingReserved); err != nil || !postingReserved {
					t.Fatal("resource reservation did not propagate monotonically")
				}
			case "gone404", "redirect_header404":
				if result.Cycle.Status != "gone_pending" || reserved || failures != 0 || gone != 1 || resource == nil || *resource != finalURL {
					t.Fatal("provider404 lost initial/final response lineage")
				}
				if f.r.Get(ctx, "host_fail:final.example.com").Val() != "2" || f.r.HGet(ctx, "board:"+f.board, "egress_host").Val() != "" {
					t.Fatal("provider disappearance advanced generic host failure")
				}
			default:
				if result.Cycle.Status != "failed" || reserved || failures != 1 || gone != 0 || resource != nil || lastError == nil {
					t.Fatal("generic/partial/blocked fetch became provider or publisher authority")
				}
				host := "final.example.com"
				if mode == "blocked_redirect" {
					host = "boards-api.greenhouse.io"
				}
				if f.r.HGet(ctx, "board:"+f.board, "egress_host").Val() != host {
					t.Fatal("generic failure lost final attempted host")
				}
				if mode != "blocked_redirect" && result.HTTP.LastStatus != map[string]int{"status503": 503, "bad_inventory": 200, "partial_reserved404": 404}[mode] {
					t.Fatal("read failure invented another response")
				}
			}
		})
	}
}

func TestRealClaimRunnerPreexistingReservationAndHostDeferralDoNotFetch(t *testing.T) {
	for _, mode := range []string{"reserved", "circuit_open"} {
		t.Run(mode, func(t *testing.T) {
			f := privatePipelineFixture(t)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			if mode == "reserved" {
				if _, err := f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", f.board); err != nil {
					t.Fatal(err)
				}
			} else {
				now, err := f.r.Time(ctx).Result()
				if err != nil {
					t.Fatal(err)
				}
				if err := f.r.Set(ctx, "host_open:job-boards.greenhouse.io", float64(now.Unix())+1800, time.Hour).Err(); err != nil {
					t.Fatal(err)
				}
			}
			var requests atomic.Int64
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { requests.Add(1); w.WriteHeader(500) }))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
			if err != nil || requests.Load() != 0 || result.HTTP.Requests != 0 {
				t.Fatal("no-fetch claim performed HTTP", err)
			}
			assertClaimReceiptSettlement(t, f, result)
			want := "publisher_reserved"
			if mode == "circuit_open" {
				want = "host_circuit_open"
			}
			if result.Cycle.Status != want {
				t.Fatal("wrong no-fetch lifecycle outcome")
			}
			var failures int
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil || failures != 0 {
				t.Fatal("no-fetch result spent failure budget")
			}
		})
	}
}

func TestRealClaimRunnerCancellationDoesNotCompletePartialResponse(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	claim, circuits := claimFixture(t, f)
	ready := make(chan struct{})
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("TDM-Reservation", "1")
		w.WriteHeader(404)
		_, _ = w.Write([]byte("partial"))
		w.(http.Flusher).Flush()
		close(ready)
		<-r.Context().Done()
	}))
	go func() {
		select {
		case <-ready:
			cancel()
		case <-ctx.Done():
		}
	}()
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
	if !errors.Is(err, context.Canceled) || result.Settled || result.Cycle != nil {
		t.Fatal("canceled body received terminal authority", err)
	}
	if result.HTTP.Requests != result.HTTP.Responses+result.HTTP.NoResponse {
		t.Fatal("canceled request conservation differs")
	}
	var reserved bool
	var failures, gone int
	if err := f.pg.QueryRow(context.Background(), "SELECT tdm_reserved,consecutive_failures,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &failures, &gone); err != nil || reserved || failures != 0 || gone != 0 {
		t.Fatal("canceled partial body changed canonical lifecycle")
	}
}

func TestClaimRunnerRejectsUninitializedSealedClient(t *testing.T) {
	result, err := RunGreenhouseClaim(context.Background(), nil, nil, &VerifiedDirectHTTP{}, &pipelinePreparer{}, nil)
	if !errors.Is(err, queue.ErrConfiguration) || result == nil || result.Settled {
		t.Fatal("uninitialized runner accepted authority")
	}
	if _, err := NewVerifiedDirectHTTP(DirectHTTPConfig{}); err == nil {
		t.Fatal("sealed client substituted system trust")
	}
}

func TestRealClaimRunnerCommitBeforeAckRecoveryDoesNotFetch(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	var requests atomic.Int64
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		// A private Redis fault after claim/fetch forces settlement to decline.
		// The guarded script must preserve the entire lease and snapshot.
		if err := f.r.Set(ctx, "ready:simple:1", "private settlement fault", 0).Err(); err != nil {
			t.Error(err)
		}
		w.WriteHeader(503)
	}))
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, &pipelinePreparer{}, circuits)
	if !errors.Is(err, queue.ErrObservation) || result.Settled || result.Cycle == nil || result.Cycle.Receipt == nil {
		t.Fatal("fault did not preserve committed receipt", err)
	}
	if requests.Load() != 1 || f.r.ZCard(ctx, "inflight:simple").Val() != 1 || f.r.HGet(ctx, "board:"+f.board, "egress_host").Val() != "" {
		t.Fatal("failed settlement removed lease/published host")
	}
	var epoch int64
	var plan string
	if err := f.pg.QueryRow(ctx, "SELECT last_value FROM lightpanda_b0_routing_epoch_seq").Scan(&epoch); err != nil {
		t.Fatal(err)
	}
	if err := f.pg.QueryRow(ctx, "SELECT plan_sha256 FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&plan); err != nil {
		t.Fatal(err)
	}
	f.a.Close()
	if err := f.r.Del(ctx, "ready:simple:1").Err(); err != nil {
		t.Fatal(err)
	}
	member := "monitor|greenhouse|" + f.board
	if err := f.r.ZAdd(ctx, "inflight:simple", redis.Z{Score: 1, Member: member}).Err(); err != nil {
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
	replacement, err := queue.OpenOwnedAuthority(ctx, f.dsn, f.client, epoch, plan, strings.Repeat("a", 40))
	if err != nil {
		t.Fatal(err)
	}
	defer replacement.Close()
	recovered, err := replacement.Claim(ctx, queue.Simple)
	if err != nil || recovered == nil || recovered.RecoveredReceipt() == nil {
		t.Fatal("committed run was not recovered", err)
	}
	result, err = RunGreenhouseClaim(ctx, replacement, recovered, client, &pipelinePreparer{}, circuits)
	if err != nil || result.Cycle.Status != "recovered" || requests.Load() != 1 || result.HTTP.Requests != 0 {
		t.Fatal("recovered runner repeated fetch", err)
	}
	assertClaimReceiptSettlement(t, f, result)
	if f.r.Get(ctx, "host_fail:boards-api.greenhouse.io").Val() != "1" || f.r.HGet(ctx, "board:"+f.board, "egress_host").Val() != "boards-api.greenhouse.io" {
		t.Fatal("runner recovery replayed host outcome/lost routing")
	}
	var count int
	if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&count); err != nil || count != 1 {
		t.Fatal("recovered run repeated canonical failure")
	}
}

func TestRealClaimRunnerMidPreparationReservationKeepsCommittedPrefix(t *testing.T) {
	f := privatePipelineFixture(t)
	ctx := context.Background()
	claim, circuits := claimFixture(t, f)
	jobs := make([]map[string]string, 1001)
	for i := range jobs {
		jobs[i] = map[string]string{"absolute_url": fmt.Sprintf("https://example.com/jobs/%s-%04d", f.company, i), "title": "Posting", "content": "<p>Posting</p>"}
	}
	body, err := json.Marshal(map[string]any{"jobs": jobs})
	if err != nil {
		t.Fatal(err)
	}
	client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(body) }))
	preparer := &reserveDuringPreparation{f: f, inner: &pipelinePreparer{}}
	result, err := RunGreenhouseClaim(ctx, f.a, claim, client, preparer, circuits)
	if err != nil || result.Cycle.Status != "publisher_reserved" {
		t.Fatal("racing reservation entered failure lifecycle", err)
	}
	assertClaimReceiptSettlement(t, f, result)
	var count, failures int
	if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count); err != nil || count != 501 {
		t.Fatal("reserved write lost committed prefix or inserted partial next chunk")
	}
	if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures); err != nil || failures != 0 {
		t.Fatal("reservation spent failure budget")
	}
}

type reserveDuringPreparation struct {
	f     nativePipelineFixture
	inner *pipelinePreparer
	seen  int
}

func (p *reserveDuringPreparation) Prepare(ctx context.Context, job RichMonitorJob) (*queue.GreenhouseRichContent, error) {
	p.seen++
	if p.seen == 751 {
		if _, err := p.f.pg.Exec(ctx, "UPDATE job_board SET tdm_reserved=true WHERE id=$1::uuid", p.f.board); err != nil {
			return nil, err
		}
	}
	return p.inner.Prepare(ctx, job)
}
