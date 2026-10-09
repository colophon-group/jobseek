package worker

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"

	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

func TestRealAccentureHTTPBrowserQueueCanonicalSettlement(t *testing.T) {
	for _, mode := range []string{"complete", "publisher", "later-failure"} {
		t.Run(mode, func(t *testing.T) {
			board := "https://www.accenture.com/us-en/careers/jobsearch"
			f := privateRichPipelineFixtureURL(t, "accenture", `{"country":"USA","language":"en","site":"us-en","scraper_type":"skip"}`, board, queue.Browser)
			ctx := context.Background()
			claim, err := f.a.Claim(ctx, queue.Browser)
			if err != nil || claim == nil {
				t.Fatal("original browser claim missing", err)
			}
			circuits, err := queue.NewHostCircuits(f.client, queue.DefaultHostCircuitSettings())
			if err != nil {
				t.Fatal(err)
			}
			client := richPipelineHTTP(t, func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "GET" {
					http.SetCookie(w, &http.Cookie{Name: "public", Value: "fixture", Path: "/"})
					fmt.Fprint(w, "<html>Public careers</html>")
					return
				}
				if r.URL.Path != "/api/accenture/elastic/findjobs" {
					t.Fatal("wrong provider API")
				}
				if mode == "publisher" {
					w.Header().Set("TDM-Reservation", "1")
					fmt.Fprint(w, "reserved malformed body")
					return
				}
				body, _ := io.ReadAll(r.Body)
				if mode == "later-failure" && strings.Contains(string(body), "name=\"startIndex\"\r\n\r\n500") {
					w.WriteHeader(404)
					return
				}
				total, count := 1, 1
				if mode == "later-failure" {
					total, count = 501, 500
				}
				rows := []map[string]any{}
				for n := 0; n < count; n++ {
					rows = append(rows, map[string]any{"guid": fmt.Sprintf("%s-%d", f.company, n), "title": "Senior Software Engineer", "jobDescription": "<p>Build Go services in Zurich.</p>", "location": "Zurich", "remoteType": "Hybrid", "postedDate": "2026-10-09"})
				}
				json.NewEncoder(w).Encode(map[string]any{"totalHits": total, "data": rows})
			})
			// The unusable renderer client proves this admitted HTTP route needs
			// no browser reservation while retaining its original queue boundary.
			renderer := &NativeRenderedDetails{client: &lp.Client{}, slots: make(chan struct{}, 1)}
			result, err := RunGreenhouseClaim(ctx, f.a, claim, client, richPipelinePreparer(t, f), circuits, renderer)
			if err != nil || result == nil || !result.Settled {
				t.Fatal("native HTTP claim did not settle", err)
			}
			assertRichDeadlineAndLease(t, f, "accenture", queue.Browser)
			var inserted, missing, failures int
			var reserved bool
			if err = f.pg.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE board_id=$1::uuid AND id<>$2::uuid", f.board, f.original).Scan(&inserted); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT missing_count FROM job_posting WHERE id=$1::uuid", f.original).Scan(&missing); err != nil {
				t.Fatal(err)
			}
			if err = f.pg.QueryRow(ctx, "SELECT consecutive_failures,tdm_reserved FROM job_board WHERE id=$1::uuid", f.board).Scan(&failures, &reserved); err != nil {
				t.Fatal(err)
			}
			if mode == "complete" {
				var title, body string
				var locations []int32
				if err = f.pg.QueryRow(ctx, "SELECT p.titles[1],p.location_ids,d.html FROM job_posting p JOIN descriptions d ON d.posting_id=p.id WHERE p.board_id=$1::uuid AND p.id<>$2::uuid", f.board, f.original).Scan(&title, &locations, &body); err != nil {
					t.Fatal(err)
				}
				if inserted != 1 || missing != 1 || failures != 0 || title != "Senior Software Engineer" || fmt.Sprint(locations) != "[2]" || !strings.Contains(body, "Build Go services") {
					t.Fatal("canonical HTTP fields or absence authority changed")
				}
			} else if inserted != 0 || missing != 0 || mode == "publisher" && (!reserved || failures != 0) || mode == "later-failure" && failures != 1 {
				t.Fatal("failed HTTP snapshot mutated canonical inventory", inserted, missing, failures, reserved)
			}
		})
	}
}
