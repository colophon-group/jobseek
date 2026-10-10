package worker

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/url"
	"strings"
	"testing"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestAccentureHTTPPublicCookiesRetryReservationAndFailedPagination(t *testing.T) {
	for _, mode := range []string{"complete", "retry", "reserved", "later_page_failure"} {
		t.Run(mode, func(t *testing.T) {
			lists, pages := 0, 0
			client := verifiedClaimFixtureClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if strings.Contains(r.Header.Get("Cookie"), "foreign_cookie") {
					t.Error("cookies leaked from another operation")
				}
				if r.URL.Path == "/us-en/careers/jobsearch" {
					lists++
					http.SetCookie(w, &http.Cookie{Name: "public_listing", Value: "fixture", Path: "/"})
					fmt.Fprint(w, "<html><body>Public jobs</body></html>")
					return
				}
				pages++
				cookie, err := r.Cookie("public_listing")
				if err != nil || cookie.Value != "fixture" || r.Method != "POST" || r.URL.Path != "/api/accenture/elastic/findjobs" || r.Header.Get("Content-Type") != "multipart/form-data; boundary=----FormBoundary" {
					t.Error("original public session or request changed")
				}
				if mode == "reserved" {
					w.Header().Set("TDM-Reservation", "1")
					fmt.Fprint(w, "unparseable reserved response")
					return
				}
				if mode == "retry" && pages == 1 {
					w.WriteHeader(503)
					return
				}
				body, _ := io.ReadAll(r.Body)
				if mode == "later_page_failure" && strings.Contains(string(body), "name=\"startIndex\"\r\n\r\n500") {
					w.WriteHeader(404)
					return
				}
				count, total := 1, 1
				if mode == "later_page_failure" {
					count, total = 500, 501
				}
				items := []map[string]any{}
				for n := 0; n < count; n++ {
					items = append(items, map[string]any{"guid": fmt.Sprint(n), "title": "Engineer", "jobDescription": "<p>Build</p>", "location": "Boston", "remoteType": "Hybrid"})
				}
				json.NewEncoder(w).Encode(map[string]any{"totalHits": map[string]any{"total": total}, "data": items})
			}))
			board := "https://www.accenture.com/us-en/careers/jobsearch"
			client.client.Jar, _ = cookiejar.New(nil)
			base, _ := url.Parse(board)
			client.client.Jar.SetCookies(base, []*http.Cookie{{Name: "foreign_cookie", Value: "fixture", Path: "/"}})
			p := queue.GreenhouseMonitorProfile{Provider: "accenture", Profile: "accenture.http-items/v1", Endpoint: board}
			config := map[string]string{"crawler_type": "accenture", "board_url": board, "metadata": `{"country":"USA","language":"en","site":"us-en","scraper_type":"skip"}`}
			waits := 0
			got, err := discoverAccentureHTTPInventoryWithWait(context.Background(), client.client, p, config, func(context.Context, time.Duration) error { waits++; return nil })
			if lists != 1 {
				t.Fatal("listing session was replayed")
			}
			switch mode {
			case "reserved":
				var reserved *policy.Reservation
				if !errors.As(err, &reserved) || pages != 1 || waits != 0 || len(got.Jobs) != 0 || got.Response == nil || !got.Response.reserved {
					t.Fatalf("reservation became retry or empty success: %v", err)
				}
			case "later_page_failure":
				if err == nil || pages != 2 || waits != 0 || len(got.Jobs) != 0 {
					t.Fatalf("failed later page published a prefix: %v", err)
				}
			default:
				if err != nil || got.Truncated || len(got.Jobs) != 1 || got.Jobs[0].Title == nil || *got.Jobs[0].Title != "Engineer" || got.Jobs[0].Description == nil || got.Jobs[0].JobLocationType != "Hybrid" {
					t.Fatalf("rich inventory changed: %v", err)
				}
				if mode == "retry" && (pages != 2 || waits != 1) || mode == "complete" && (pages != 1 || waits != 0) {
					t.Fatal("original retry budget changed")
				}
			}
		})
	}
}

func TestAccentureHTTPRejectsCapturedJobSearchAndUnknownControls(t *testing.T) {
	board := "https://www.accenture.com/us-en/careers/jobsearch"
	p := queue.GreenhouseMonitorProfile{Provider: "accenture", Profile: "accenture.http-items/v1", Endpoint: board}
	for _, metadata := range []string{`{"country":"USA","language":"en","site":"us-en","endpoint":"jobsearch/result"}`, `{"country":"USA","language":"en","site":"us-en","response_body_limit":67108864}`} {
		got, err := discoverAccentureHTTPInventory(context.Background(), http.DefaultClient, p, map[string]string{"crawler_type": "accenture", "board_url": board, "metadata": metadata})
		if !errors.Is(err, queue.ErrConfiguration) || len(got.Jobs) != 0 {
			t.Fatal("unqualified route or control admitted")
		}
	}
}
