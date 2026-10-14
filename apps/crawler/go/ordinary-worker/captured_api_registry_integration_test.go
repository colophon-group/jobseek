package worker

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func capturedAPIWorkerRegistry(t *testing.T, slug string) (string, string) {
	t.Helper()
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	columns := map[string]int{}
	for i, k := range rows[0] {
		columns[k] = i
	}
	for _, row := range rows[1:] {
		if row[columns["board_slug"]] != slug {
			continue
		}
		var md map[string]any
		if json.Unmarshal([]byte(row[columns["monitor_config"]]), &md) != nil {
			t.Fatal("invalid published metadata")
		}
		md["scraper_type"] = row[columns["scraper_type"]]
		if raw := row[columns["scraper_config"]]; raw != "" {
			var detail any
			if json.Unmarshal([]byte(raw), &detail) != nil {
				t.Fatal("invalid published detail config")
			}
			md["scraper_config"] = detail
		}
		raw, _ := json.Marshal(md)
		return row[columns["board_url"]], string(raw)
	}
	t.Fatal("published candidate missing", slug)
	return "", ""
}

func TestRealCapturedAPIRegistryCanonicalEffectsAndTerminalConservation(t *testing.T) {
	for _, slug := range []string{"airbnb-contractors", "didi-global-careers-intl", "bp-travelcenters"} {
		for _, mode := range []string{"complete", "reserved", "failed"} {
			t.Run(slug+"/"+mode, func(t *testing.T) {
				board, metadata := capturedAPIWorkerRegistry(t, slug)
				worker := queue.Simple
				if slug == "bp-travelcenters" {
					worker = queue.Browser
				}
				detailWorker := queue.Simple
				if slug == "bp-travelcenters" {
					detailWorker = queue.Browser
				}
				f := privateRichPipelineFixtureURL(t, "api_sniffer", metadata, board, worker, detailWorker)
				ctx := context.Background()
				claim, err := f.a.Claim(ctx, worker)
				if err != nil || claim == nil {
					t.Fatal("qualified claim unavailable", err)
				}
				circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
				if err != nil {
					t.Fatal(err)
				}
				calls := 0
				handler := func(w http.ResponseWriter, r *http.Request) {
					calls++
					if worker == queue.Browser {
						t.Error("browser replay used worker direct HTTP")
						w.WriteHeader(500)
						return
					}
					if slug == "didi-global-careers-intl" && (r.Method != "POST" || r.Host != "cdncareers.didiglobal.com:34003") {
						t.Error("exact original POST/port changed")
					}
					if slug == "airbnb-contractors" && r.Header.Get("X-Tenant") != "magnit-airbnb" {
						t.Error("anonymous public tenant header lost")
					}
					if mode != "complete" {
						if mode == "reserved" {
							w.Header().Set("Tdm-Reservation", "1")
						}
						w.WriteHeader(503)
						return
					}
					w.Header().Set("Content-Type", "application/json")
					if slug == "airbnb-contractors" {
						fmt.Fprintf(w, `{"meta":{"totalCount":2},"data":[{"id":%q,"title":{"name":"Senior Software Engineer"},"description":"<p>Build Go services in Zurich.</p>","type":"contract","workplaceType":"remote"},{"id":%q,"title":{"name":"Senior Software Engineer"},"description":"<p>Build Go services in Zurich.</p>","type":"contract","workplaceType":"remote"}]}`, f.company+"-1", f.company+"-2")
					} else {
						fmt.Fprintf(w, `{"result":[{"id":%q,"jobTitle":"Senior Software Engineer","jobType":"Contract","team":"Engineering"},{"id":%q,"jobTitle":"Senior Software Engineer","jobType":"Contract","team":"Engineering"}]}`, f.company+"-1", f.company+"-2")
					}
				}
				client := richPipelineHTTP(t, handler)
				if slug == "didi-global-careers-intl" {
					client = explicitTLSFixtureClient(t, handler, true, false, "cdncareers.didiglobal.com:34003")
				}
				renderer := heldMonitor(func(_ context.Context, p queue.GreenhouseMonitorProfile, _ map[string]string) (RichDiscovery, error) {
					if worker != queue.Browser {
						t.Fatal("HTTP route used browser replay")
					}
					r := replay.Response{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: p.EffectiveConfigSHA256, Outcome: "success"}
					r.Inventory = json.RawMessage(fmt.Sprintf(`{"Jobs":[{"url":%q,"title":"Senior Software Engineer","locations":["Zurich"]},{"url":%q,"title":"Senior Software Engineer","locations":["Zurich"]}],"Truncated":false}`, board+"/requisition/"+f.company+"-1", board+"/requisition/"+f.company+"-2"))
					if mode == "reserved" {
						r.Outcome = "publisher_reserved"
						r.Inventory = nil
						r.Reservation = &replay.Reservation{URL: p.Endpoint, Source: "header"}
					}
					if mode == "failed" {
						r.Outcome = "failed"
						r.Inventory = nil
					}
					return parseAPIReplayResponse(p, r)
				})
				result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
				if err != nil || result == nil || !result.Settled {
					t.Fatal("terminal settlement unavailable", err)
				}
				assertRichDeadlineAndLease(t, f, "api_sniffer", worker)
				var inserted, missing, failures, validTitles int
				var reserved bool
				if err = f.pg.QueryRow(ctx, `SELECT (SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id),p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id AND titles[1]='Senior Software Engineer') FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&inserted, &missing, &failures, &reserved, &validTitles); err != nil {
					t.Fatal(err)
				}
				if mode == "complete" {
					if inserted != 2 || validTitles != 2 || missing != 1 || failures != 0 || reserved {
						t.Fatal("complete inventory effects changed", inserted, missing, failures)
					}
					if slug == "airbnb-contractors" {
						var descriptions int
						if err = f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid AND d.html LIKE '%Build Go services%' AND NOT d.r2_uploaded AND p.description_r2_hash IS NULL AND p.employment_type='contract'`, f.board, f.original).Scan(&descriptions); err != nil || descriptions != 2 {
							t.Fatal("rich content/R2 authority changed", err, descriptions)
						}
					} else {
						var details int
						// Original rich API discovery delegates detail work only through
						// explicit scraper enrichment. DiDi requests description; BP does not.
						wantDetails := 2
						if slug == "bp-travelcenters" {
							wantDetails = 0
						}
						if err = f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND next_scrape_at IS NOT NULL`, f.board, f.original).Scan(&details); err != nil || details != wantDetails {
							t.Fatal("original independent detail work lost", err, details)
						}
					}
				} else {
					want := 1
					if mode == "reserved" {
						want = 0
					}
					if inserted != 0 || missing != 0 || failures != want || reserved != (mode == "reserved") {
						t.Fatal("failure/policy changed absence authority", inserted, missing, failures, reserved)
					}
				}
			})
		}
	}
}
