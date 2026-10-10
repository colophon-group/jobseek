//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestLightpandaBrassRingCommittedPaginationIntegration(t *testing.T) {
	binary := integrationBinary(t)
	for _, mode := range []string{"complete", "publisher_reserved", "duplicate"} {
		t.Run(mode, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t, "sjobs.brassring.com")
			var mu sync.Mutex
			requests := []string{}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == "GET" {
					http.SetCookie(w, &http.Cookie{Name: "brass-session", Value: "private-brass-cookie", Path: "/", Secure: true, HttpOnly: true})
					w.Header().Set("Content-Type", "text/html")
					io.WriteString(w, `<!doctype html><html><body>
<button id="clearResumeJobsBtn" onclick="loadPage(1,false)">Search</button>
<select id="sortBy"><option value="0">Date</option><option value="1">Alphabetical</option></select>
<button id="sortBy-button" onclick="document.getElementById('sortBy-menu').style.display='block'">Sort</button>
<ul id="sortBy-menu" style="display:none"><li>Date</li><li onclick="loadPage(1,true)">Alphabetical</li></ul>
<button title="Next Page" onclick="loadPage(Number(document.getElementById('current').textContent)+1,true)">Next</button>
<button class="pagewise-pagination" aria-current="page" id="current">1</button>
<script>async function loadPage(page,stable){const path=!stable?'/TgNewUI/Search/Ajax/MatchedJobs':'/TgNewUI/Search/Ajax/ProcessSortAndShowMoreJobs';await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({page:page,stable:stable})});setTimeout(()=>{document.getElementById('current').textContent=String(page)},150)}</script>
</body></html>`)
					return
				}
				cookie, cookieErr := r.Cookie("brass-session")
				var request struct {
					Page   int
					Stable bool
				}
				if cookieErr != nil || cookie.Value != "private-brass-cookie" || json.NewDecoder(r.Body).Decode(&request) != nil {
					http.Error(w, "public browser session missing", 403)
					return
				}
				mu.Lock()
				requests = append(requests, fmt.Sprintf("%d/%t", request.Page, request.Stable))
				mu.Unlock()
				w.Header().Set("Content-Type", "application/json")
				if mode == "publisher_reserved" && request.Page == 2 {
					w.Header().Set("TDM-Reservation", "1")
				}
				id := request.Page
				if mode == "duplicate" && request.Page == 3 {
					id = 2
				}
				questions := []map[string]string{{"QuestionName": "reqid", "Value": fmt.Sprint(id)}, {"QuestionName": "jobtitle", "Value": "Engineer"}, {"QuestionName": "jobdescription", "Value": "<p>Build</p>"}, {"QuestionName": "formtext8", "Value": "Boston"}}
				row := map[string]any{"Link": fmt.Sprintf("https://sjobs.brassring.com/TGnewUI/Search/home/HomeWithPreLoad?partnerid=25416&siteid=5998&PageType=JobDetails&jobid=%d", id), "Questions": questions}
				json.NewEncoder(w).Encode(map[string]any{"JobsCount": 3, "Jobs": map[string]any{"Job": []any{row}}})
			}))
			listener, err := net.Listen("tcp4", "127.0.0.2:0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxy(t, origin, "sjobs.brassring.com")
			board := "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=5998"
			var inventory api.Inventory
			task, err := newBrassRingBrowserTask(board, `{}`, func(ctx context.Context, load api.BrassRingPageLoader) error {
				var err error
				inventory, err = api.CollectBrassRingSnapshot(ctx, api.BrassRingBoard{PartnerID: "25416", SiteID: "5998"}, load, func(s string) (*string, error) { return &s, nil })
				return err
			})
			if err != nil {
				t.Fatal(err)
			}
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			result, err := runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy(), TaskTimeout: 25 * time.Second}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			if mode == "complete" {
				encoded, _ := json.Marshal(inventory)
				if err != nil || !result.apiReplaySessionSettled || len(inventory.Jobs) != 3 || inventory.Truncated || strings.Contains(string(encoded), "private-brass-cookie") {
					t.Fatal("physical committed snapshot failed", err)
				}
			} else {
				if err == nil || errors.Is(err, errCleanupUnproved) || len(inventory.Jobs) != 0 {
					t.Fatal("invalid snapshot or cleanup admitted", err)
				}
				if mode == "publisher_reserved" {
					var reserved *policy.Reservation
					if !errors.As(err, &reserved) {
						t.Fatal("publisher reservation lost", err)
					}
				}
				if mode == "duplicate" && !errors.Is(err, api.ErrBrassRingSnapshot) {
					t.Fatal("snapshot retry class lost", err)
				}
			}
			mu.Lock()
			defer mu.Unlock()
			want := []string{"1/false", "1/true", "2/true", "3/true"}
			if mode == "publisher_reserved" {
				want = want[:3]
			}
			if !reflect.DeepEqual(requests, want) {
				t.Fatal("capture/sort/committed-page order changed", requests)
			}
		})
	}
}
