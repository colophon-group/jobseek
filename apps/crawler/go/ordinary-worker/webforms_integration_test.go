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

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func webFormsFixtureMetadata(t *testing.T) string {
	t.Helper()
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil || len(rows) < 2 {
		t.Fatal("canonical fixture CSV unavailable", err)
	}
	keys := map[string]int{}
	for i, key := range rows[0] {
		keys[key] = i
	}
	for _, row := range rows[1:] {
		if row[keys["board_slug"]] != "slaughter-and-may-careers" {
			continue
		}
		var metadata map[string]any
		if json.Unmarshal([]byte(row[keys["monitor_config"]]), &metadata) != nil {
			t.Fatal("canonical actions unavailable")
		}
		metadata["scraper_type"] = "json-ld"
		body, err := json.Marshal(metadata)
		if err != nil {
			t.Fatal(err)
		}
		return string(body)
	}
	t.Fatal("canonical WebForms fixture missing")
	return ""
}

func TestRealWebFormsCompleteSnapshotPublisherPolicyAndQueueConservation(t *testing.T) {
	for _, mode := range []string{"complete", "bootstrap-reserved", "post-reserved", "later-failure", "partial", "changed-total"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "dom", webFormsFixtureMetadata(t), slaughterListing, queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("browser boundary claim unavailable", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				calls++
				if "https://"+r.Host+r.URL.String() != slaughterListing {
					t.Error("form request left publisher scope")
				}
				body := webFormsFixture(t, 10, 2)
				if calls == 1 {
					if r.Method != "GET" {
						t.Error("bootstrap is not GET")
					}
					if mode == "bootstrap-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
					}
				} else {
					if r.Method != "POST" || calls != 2 {
						t.Error("unexpected postback")
					}
					body = webFormsFixture(t, 50, 2)
					if mode == "post-reserved" {
						w.Header().Set("TDM-Reservation", "1")
						w.WriteHeader(503)
					}
					if mode == "later-failure" {
						w.WriteHeader(503)
					}
					if mode == "partial" {
						body = strings.Replace(body, "VacancyId=2", "InvalidId=2", 1)
					}
					if mode == "changed-total" {
						body = webFormsFixture(t, 50, 3)
					}
				}
				fmt.Fprint(w, body)
			})
			// The published form route retains the browser queue but needs no
			// renderer connection or slot to produce its complete HTTP snapshot.
			renderer := &NativeRenderedDetails{client: &lp.Client{}, slots: make(chan struct{}, 1)}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("form inventory did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "dom", queue.Browser)
			var inserted, missing, failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, `SELECT p.missing_count,b.consecutive_failures,b.tdm_reserved,(SELECT count(*) FROM job_posting WHERE board_id=b.id AND id<>p.id) FROM job_posting p JOIN job_board b ON b.id=p.board_id WHERE p.id=$1::uuid`, f.original).Scan(&missing, &failures, &reserved, &inserted); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if inserted != 2 || missing != 1 || failures != 0 || reserved {
					t.Fatal("complete snapshot effects changed")
				}
				var rich int
				if err := f.pg.QueryRow(ctx, `SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid AND cardinality(titles)>0`, f.board, f.original).Scan(&rich); err != nil || rich != 0 {
					t.Fatal("URL-only inventory acquired rich fields", err)
				}
				if f.r.ZCard(ctx, "ft_scrapes_simple:careers.slaughterandmay.com").Val() != 2 {
					t.Fatal("independent detail queue lost URLs")
				}
			} else {
				wantFailures := 1
				if strings.Contains(mode, "reserved") {
					wantFailures = 0
				}
				if inserted != 0 || missing != 0 || failures != wantFailures || reserved != strings.Contains(mode, "reserved") {
					t.Fatal("partial form snapshot changed inventory or policy", inserted, missing, failures, reserved)
				}
			}
		})
	}
}
