package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type unifrHTTPReference struct {
	Name      string
	Responses map[string]string
	Error     bool
}

func unifrHTTPReferences(t *testing.T) []unifrHTTPReference {
	t.Helper()
	raw, err := os.ReadFile("../api-sniffer-monitor/testdata/python_unifr_discovery.json")
	var cases []unifrHTTPReference
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 4 {
		t.Fatal("actual Python Unifr reference missing", err)
	}
	// The parser corpus fixes its clock in August 2026. HTTP execution uses the
	// current clock, so keep the same payload contract with a future expiry.
	for i := range cases {
		for resource, body := range cases[i].Responses {
			cases[i].Responses[resource] = strings.ReplaceAll(body, "2026-09-30", "2050-09-30")
		}
	}
	return cases
}

func unifrHTTPHandler(t *testing.T, c unifrHTTPReference, mode string) http.HandlerFunc {
	t.Helper()
	var lock sync.Mutex
	seen := map[string]int{}
	return func(w http.ResponseWriter, r *http.Request) {
		resource := "https://" + r.Host + r.URL.RequestURI()
		body, found := c.Responses[resource]
		if !found || r.Method != "GET" || r.Header.Get("Authorization") != "" {
			t.Error("unbound request", resource)
			w.WriteHeader(400)
			return
		}
		lock.Lock()
		seen[resource]++
		attempt := seen[resource]
		lock.Unlock()
		if resource == api.UnifrCentralFR {
			if _, err := r.Cookie("unifr-fixture"); err == nil {
				t.Error("cookie from earlier invocation reused")
			}
			http.SetCookie(w, &http.Cookie{Name: "unifr-fixture", Value: "tenant", Path: "/"})
		}
		if resource == api.UnifrCentralDE {
			if cookie, err := r.Cookie("unifr-fixture"); err != nil || cookie.Value != "tenant" {
				t.Error("locale cookie lost")
			}
		}
		kind := "text/html; charset=utf-8"
		if strings.HasPrefix(resource, api.UnifrDetailRoot) {
			kind = "application/json; charset=utf-8"
		}
		w.Header().Set("Content-Type", kind)
		if resource == api.UnifrCentralDE && mode == "retry" && attempt == 1 {
			w.WriteHeader(503)
			return
		}
		if strings.HasSuffix(resource, "/de/1897") {
			switch mode {
			case "reserved":
				w.Header().Set("TDM-Reservation", "1")
			case "media":
				w.Header().Set("Content-Type", "text/plain")
			case "redirect":
				w.Header().Set("Location", "https://other.example/jobs")
				w.WriteHeader(302)
				return
			case "failed":
				w.WriteHeader(503)
				return
			}
		}
		fmt.Fprint(w, body)
	}
}

func TestUnifrHTTPCompleteLocaleFieldsCookiesRetriesAndLateFailures(t *testing.T) {
	cases := unifrHTTPReferences(t)
	for _, mode := range []string{"complete", "retry", "reserved", "media", "redirect", "failed", "late-id", "late-owner", "late-description"} {
		t.Run(mode, func(t *testing.T) {
			c := cases[0]
			switch mode {
			case "late-id":
				c = cases[1]
			case "late-owner":
				c = cases[2]
			case "late-description":
				c = cases[3]
			}
			client := verifiedClaimFixtureClient(t, unifrHTTPHandler(t, c, mode))
			var waits atomic.Int32
			for iteration := 0; iteration < 2; iteration++ {
				out, err := FetchUnifrHTTP(context.Background(), client.client, queue.GreenhouseMonitorProfile{Provider: "unifr", Profile: "unifr.authoritative-items/v1", Endpoint: api.UnifrCentralFR}, map[string]string{"board_url": api.UnifrCentralFR, "metadata": `{"source":"central","scraper_type":"skip"}`, "monitor_needs_browser": "0"}, func(context.Context, time.Duration) error { waits.Add(1); return nil })
				complete := mode == "complete" || mode == "retry"
				if (err == nil) != complete || !complete && len(out.Jobs) != 0 {
					t.Fatal("late failure exposed prefix", err, out)
				}
				if mode == "reserved" {
					var reservation *policy.Reservation
					if !errors.As(err, &reservation) {
						t.Fatal("publisher reservation lost", err)
					}
				}
				if complete {
					if len(out.Jobs) != 3 {
						t.Fatal(out)
					}
					job := out.Jobs[1]
					if job.Title == nil || *job.Title != "Titre français" || job.Description == nil || *job.Description != "<p>fr detail for 1891</p>" || len(job.Locations) != 1 || job.Locations[0] != "Fribourg, Switzerland" || len(job.LocalizationLocales) != 2 || job.LocalizationLocales[0] != "de" || job.LocalizationLocales[1] != "fr" || len(job.LocalizedTitles) != 2 || job.URLOnly {
						t.Fatal("localized fields differ", job)
					}
				}
			}
			if mode == "retry" && waits.Load() != 1 {
				t.Fatal("bounded retry missing", waits.Load())
			}
			if mode == "failed" && waits.Load() != 4 {
				t.Fatal("failure retry budget differs", waits.Load())
			}
		})
	}
}

func TestRealUnifrCompleteFailedAndReservedCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"complete", "failed", "reserved"} {
		t.Run(mode, func(t *testing.T) {
			f := privateRichPipelineFixtureURL(t, "unifr", `{"source":"central","scraper_type":"skip"}`, api.UnifrCentralFR)
			ctx := context.Background()
			claim, circuits := claimFixture(t, f)
			client := verifiedClaimFixtureClient(t, unifrHTTPHandler(t, unifrHTTPReferences(t)[0], mode))
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits)
			if err != nil || result == nil || !result.Settled {
				t.Fatal(err, result)
			}
			assertRichDeadlineAndLease(t, f, "unifr")
			var failures int
			var reserved bool
			if err := f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				if result.Batches.Inserted != 3 || failures != 0 || reserved {
					t.Fatal(result.Batches, failures, reserved)
				}
				var descriptions, localized int
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM descriptions d JOIN job_posting j ON j.id=d.posting_id WHERE j.board_id=$1::uuid AND j.id<>$2::uuid AND NOT d.r2_uploaded", f.board, f.original).Scan(&descriptions); err != nil || descriptions != 3 {
					t.Fatal("description/R2 intent lost", descriptions, err)
				}
				if err := f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND source_url LIKE '%_jid=1891' AND cardinality(titles)=2", f.board).Scan(&localized); err != nil || localized != 1 {
					t.Fatal("localized canonical titles lost", localized, err)
				}
				return
			}
			var active bool
			var title string
			if err := f.pg.QueryRow(ctx, "SELECT is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.original).Scan(&active, &title); err != nil {
				t.Fatal(err)
			}
			if !active || title != "Original" || result.Batches.Inserted != 0 || reserved != (mode == "reserved") || mode == "failed" && failures != 1 {
				t.Fatal("failed inventory changed canonical rows", active, title, failures, reserved, result.Batches)
			}
		})
	}
}
