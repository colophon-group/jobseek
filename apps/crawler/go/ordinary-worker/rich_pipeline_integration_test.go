package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"strings"
	"testing"
	"time"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func richPipelinePreparer(t *testing.T, f nativePipelineFixture) NativeRichPreparer {
	t.Helper()
	ctx := context.Background()
	store, err := executor.OpenOrdinaryLookupStore(ctx, privatePipelineReferenceDSN(t, f))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(store.Close)
	locations, err := executor.LoadLocations(ctx, store)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = locations.Close() })
	lookups, err := executor.LoadLookups(ctx, store)
	if err != nil {
		t.Fatal(err)
	}
	matcher, err := enrichment.Load("../../data")
	if err != nil {
		t.Fatal(err)
	}
	return NativeRichPreparer{&executor.Processor{Matcher: matcher, Lookups: lookups, Locations: locations}}
}

func richPipelineHTTP(t *testing.T, handler http.HandlerFunc) *VerifiedDirectHTTP {
	t.Helper()
	server := httptest.NewTLSServer(handler)
	t.Cleanup(server.Close)
	client, transport := directFixtureClient(t, server)
	transport.inner.TLSClientConfig.ServerName = "example.com"
	transport.lookup = func(context.Context, string) ([]netip.Addr, error) {
		return []netip.Addr{netip.MustParseAddr("8.8.8.8")}, nil
	}
	transport.dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "8.8.8.8:443" {
			t.Error("provider address was not validated and pinned")
		}
		return (&net.Dialer{}).DialContext(ctx, network, strings.TrimPrefix(server.URL, "https://"))
	}
	return &VerifiedDirectHTTP{client: client}
}

func TestRealOwnedRichProvidersPersistPrepareAndSettle(t *testing.T) {
	for _, provider := range []string{"ashby", "lever"} {
		t.Run(provider, func(t *testing.T) {
			f := privateRichPipelineFixture(t, provider, `{"token":"Fixture Board","scraper_type":"skip"}`)
			ctx := context.Background()
			preparer := richPipelinePreparer(t, f)
			claim, err := f.a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal("native provider claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			postingURL := "https://example.com/job/" + f.company
			description := "<p>Python. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>"
			var payload []byte
			if provider == "ashby" {
				payload, _ = json.Marshal(map[string]any{"jobs": []any{map[string]any{"jobUrl": postingURL, "title": "Senior Software Engineer", "descriptionHtml": description, "location": "Zurich", "employmentType": "FullTime", "workplaceType": "Remote"}}})
			} else {
				payload, _ = json.Marshal([]any{map[string]any{"hostedUrl": postingURL, "text": "Senior Software Engineer", "description": description, "categories": map[string]any{"location": "Zurich", "commitment": "Full-time"}, "workplaceType": "remote"}})
			}
			requests := 0
			httpClient := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				requests++
				if !strings.Contains(r.URL.EscapedPath(), "Fixture%20Board") {
					t.Error("provider token escaping changed")
				}
				if provider == "lever" && r.Header.Get("Accept") != "application/json" {
					t.Error("Lever JSON Accept lost")
				}
				_, _ = w.Write(payload)
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, httpClient, preparer, circuits)
			if err != nil || !result.Settled || result.Batches.Inserted != 1 || result.Cycle.Gone != 1 || requests != 1 {
				t.Fatalf("provider did not persist/settle complete inventory: %+v error=%v", result, err)
			}
			var title, employment, currency string
			var locationIDs, technologyIDs []int32
			var locationTypes []string
			if err := f.pg.QueryRow(ctx, `SELECT titles[1],employment_type,salary_currency,location_ids,technology_ids,location_types FROM job_posting WHERE source_url=$1`, postingURL).Scan(&title, &employment, &currency, &locationIDs, &technologyIDs, &locationTypes); err != nil {
				t.Fatal(err)
			}
			if title != "Senior Software Engineer" || employment != "full_time" || currency != "CHF" || fmt.Sprint(locationIDs) != "[2]" || fmt.Sprint(technologyIDs) != "[4]" || fmt.Sprint(locationTypes) != "[remote]" {
				t.Fatalf("native rich fields differ: %s %s %s %v %v %v", title, employment, currency, locationIDs, technologyIDs, locationTypes)
			}
			var stored string
			var uploaded bool
			if err := f.pg.QueryRow(ctx, `SELECT d.html,d.r2_uploaded FROM descriptions d JOIN job_posting jp ON jp.id=d.posting_id WHERE jp.source_url=$1`, postingURL).Scan(&stored, &uploaded); err != nil || stored != description || uploaded {
				t.Fatal("native description bytes/pending upload lost", err)
			}
			assertRichDeadlineAndLease(t, f, provider)
		})
	}
}

func assertRichDeadlineAndLease(t *testing.T, f nativePipelineFixture, provider string) {
	t.Helper()
	ctx := context.Background()
	var due time.Time
	if err := f.pg.QueryRow(ctx, "SELECT next_check_at FROM job_board WHERE id=$1::uuid", f.board).Scan(&due); err != nil {
		t.Fatal(err)
	}
	score, err := f.r.ZScore(ctx, "monitors_simple:"+provider, f.board).Result()
	if err != nil || score != float64(due.UnixMicro())/1e6 || f.r.ZCard(ctx, "inflight:simple").Val() != 0 {
		t.Fatal("canonical/native deadline or claim conservation changed", err)
	}
}

func TestRealOwnedLeverLaterFailureAndReservationHaveNoPartialWrites(t *testing.T) {
	for _, mode := range []string{"later_404", "later_reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixture(t, "lever", `{"token":"fixture","scraper_type":"skip"}`)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Simple)
			if err != nil || claim == nil {
				t.Fatal(err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			jobs := []any{}
			for i := 0; i < 100; i++ {
				jobs = append(jobs, map[string]any{"hostedUrl": fmt.Sprintf("https://example.com/job/%s/%d", f.company, i), "text": "Engineer", "description": "<p>Build</p>"})
			}
			body, _ := json.Marshal(jobs)
			requests := 0
			httpClient := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				requests++
				if r.URL.Query().Get("skip") == "0" {
					_, _ = w.Write(body)
					return
				}
				if mode == "later_404" {
					w.WriteHeader(404)
					return
				}
				w.Header().Set("TDM-Reservation", "1")
				w.Header().Set("TDM-Policy", "https://example.com/policy")
				_, _ = w.Write([]byte("[]"))
			})
			result, err := RunGreenhouseClaim(ctx, f.a, claim, httpClient, &pipelinePreparer{}, circuits)
			if err != nil || !result.Settled || requests != 2 || result.Batches.Inserted != 0 {
				t.Fatalf("later failure/policy did not settle safely: %+v error=%v", result, err)
			}
			var count, active int
			if err := f.pg.QueryRow(ctx, "SELECT count(*),count(*) FILTER(WHERE is_active) FROM job_posting WHERE board_id=$1::uuid", f.board).Scan(&count, &active); err != nil || count != 1 || active != 1 {
				t.Fatal("partial Lever inventory inserted/delisted postings", err)
			}
			var reserved bool
			var goneChecks int
			var evidence []byte
			if err := f.pg.QueryRow(ctx, "SELECT tdm_reserved,gone_confirmation_count,tdm_reservation FROM job_board WHERE id=$1::uuid", f.board).Scan(&reserved, &goneChecks, &evidence); err != nil {
				t.Fatal(err)
			}
			if goneChecks != 0 || reserved != (mode == "later_reserved") {
				t.Fatal("later page response became provider-gone or lost reservation")
			}
			if reserved && (!strings.Contains(string(evidence), "skip=100") || !strings.Contains(string(evidence), "https://example.com/policy")) {
				t.Fatal("publisher policy lost actual later-page evidence")
			}
			assertRichDeadlineAndLease(t, f, "lever")
		})
	}
}
