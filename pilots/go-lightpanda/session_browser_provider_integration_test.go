//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

// Real release, HTTPS, cookies, AJAX/form navigation, publisher policy and
// process/port cleanup. The established test transport grants no runtime proxy.
func TestLightpandaSessionProviderIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	for _, mode := range []string{"brass-pointer", "brass-complete", "brass-publisher", "brass-root-publisher", "brass-detail-publisher", "brass-duplicate", "brass-changed-total", "brass-recover", "brass-cleanup", "njoyn-complete", "njoyn-publisher", "njoyn-root-publisher", "njoyn-short-tail"} {
		t.Run(mode, func(t *testing.T) {
			provider, host, board, metadata := "brassring", "sjobs.brassring.com", "https://sjobs.brassring.com/TGnewUI/Search/Home/Home?partnerid=25416&siteid=6007", `{"scraper_type":"skip"}`
			if strings.HasPrefix(mode, "njoyn") {
				provider, host, board, metadata = "njoyn", "cgi.njoyn.com", "https://cgi.njoyn.com/corp/xweb/xweb.asp?clid=21001&page=joblisting", `{"scraper_type":"json-ld","page_wait_ms":0,"page_change_timeout_ms":1000,"snapshot_attempts":1}`
			}
			certificate, ca := dayforceOriginTLS(t, host)
			var mu sync.Mutex
			requests := []string{}
			rootLoads := 0
			brassJob := func(id string, location bool) map[string]any {
				questions := []any{map[string]any{"QuestionName": "reqid", "Value": id}, map[string]any{"QuestionName": "jobtitle", "Value": "Engineer " + id}, map[string]any{"QuestionName": "jobdescription", "Value": "<p>Build Go services.</p>"}, map[string]any{"QuestionName": "lastupdated", "Value": "13-Oct-2026"}}
				if location {
					questions = append(questions, map[string]any{"QuestionName": "formtext8", "Value": "Zurich"})
				}
				return map[string]any{"Questions": questions, "Link": board + "&jobid=" + id}
			}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				requests = append(requests, r.Method+" "+r.URL.Path)
				mu.Unlock()
				if provider == "brassring" {
					if strings.Contains(r.URL.Path, "/Ajax/") {
						cookie, e := r.Cookie("session")
						if e != nil || cookie.Value != "private-session-cookie" {
							http.Error(w, "missing session", 403)
							return
						}
						if r.Method != "POST" {
							http.Error(w, "wrong method", 400)
							return
						}
						page := r.URL.Query().Get("page")
						if mode == "brass-publisher" && page == "3" {
							w.Header().Set("Tdm-Reservation", "1")
							w.WriteHeader(503)
							return
						}
						rows := []any{brassJob("90", true), brassJob("91", true)}
						switch page {
						case "1":
							rows = []any{brassJob("1", true), brassJob("2", true)}
						case "2":
							rows = []any{brassJob("3", false), brassJob("4", true)}
						case "3":
							rows = []any{brassJob("5", true)}
						}
						mu.Lock()
						firstPass := rootLoads == 1
						mu.Unlock()
						if (mode == "brass-duplicate" || mode == "brass-recover" && firstPass) && page == "3" {
							rows = []any{brassJob("1", true)}
						}
						total := 5
						if mode == "brass-changed-total" && page == "3" {
							total = 6
						}
						w.Header().Set("Content-Type", "application/json")
						json.NewEncoder(w).Encode(map[string]any{"JobsCount": total, "Jobs": map[string]any{"Job": rows}})
						return
					}
					if id := r.URL.Query().Get("jobid"); id != "" {
						if mode == "brass-detail-publisher" {
							w.Header().Set("Tdm-Reservation", "1")
						}
						payload, _ := json.Marshal(map[string]any{"JobId": id, "Jobdetails": map[string]any{"JobDetailQuestions": []any{map[string]any{"VerityZone": "formtext8", "AnswerValue": "Basel"}}}})
						fmt.Fprintf(w, `<input id="preLoadJSON" value="%s">`, html.EscapeString(string(payload)))
						return
					}
					http.SetCookie(w, &http.Cookie{Name: "session", Value: "private-session-cookie", Path: "/", Secure: true, HttpOnly: true})
					mu.Lock()
					rootLoads++
					mu.Unlock()
					if mode == "brass-root-publisher" {
						w.Header().Set("Tdm-Reservation", "1")
						w.WriteHeader(503)
					}
					w.Header().Set("Content-Type", "text/html")
					fixture := `<!doctype html><html><body><button id="clearResumeJobsBtn" onclick="search()">Search</button><select id="sortBy"><option value="0">Recent</option><option value="1">Title</option></select><button id="sortBy-button">Sort</button><ul id="sortBy-menu"><li>Recent</li><li onclick="sorted()">Title</li></ul><span class="pagewise-pagination" aria-current="page">1</span><button title="Next Page" onclick="next()">Next</button><script>let current=1;async function request(path,n){await fetch('/TGnewUI/Search/Ajax/'+path+'?page='+n,{method:'POST'});setTimeout(()=>{current=Math.max(1,n);document.querySelector('.pagewise-pagination').textContent=String(current)},75)}function search(){request('MatchedJobs',0)}function sorted(){request('ProcessSortAndShowMoreJobs',1)}function next(){request('ProcessSortAndShowMoreJobs',current+1)}</script></body></html>`
					if mode == "brass-pointer" {
						// The upstream widget commits a pointer sequence before
						// accepting click. HTMLElement.click alone cannot drive it.
						fixture = strings.ReplaceAll(fixture, `onclick="`, `onmousedown="this.dataset.down='1'" onmouseup="this.dataset.up=this.dataset.down" onclick="if(this.dataset.up!=='1')return;delete this.dataset.down;delete this.dataset.up;`)
						fixture = strings.Replace(fixture, `<button id="sortBy-button">`, `<button id="sortBy-button" onmousedown="this.dataset.down='1'" onmouseup="this.dataset.up=this.dataset.down" onclick="if(this.dataset.up==='1')document.querySelector('#sortBy-menu').style.display='block'">`, 1)
						fixture = strings.Replace(fixture, `<ul id="sortBy-menu">`, `<ul id="sortBy-menu" style="display:none">`, 1)
					}
					io.WriteString(w, fixture)
					return
				}
				if r.Method == "GET" {
					http.SetCookie(w, &http.Cookie{Name: "session", Value: "private-session-cookie", Path: "/", Secure: true, HttpOnly: true})
					if mode == "njoyn-root-publisher" {
						w.Header().Set("Tdm-Reservation", "1")
						w.WriteHeader(503)
					}
				}
				index := 1
				if r.Method == "POST" {
					cookie, e := r.Cookie("session")
					if e != nil || cookie.Value != "private-session-cookie" {
						http.Error(w, "missing session", 403)
						return
					}
					if r.ParseForm() != nil {
						http.Error(w, "bad form", 400)
						return
					}
					index, _ = strconv.Atoi(r.PostForm.Get("pn"))
				}
				if mode == "njoyn-publisher" && index == 3 {
					w.Header().Set("Tdm-Reservation", "1")
				}
				w.Header().Set("Content-Type", "text/html")
				fmt.Fprintf(w, `<!doctype html><html><body><p>Search Results (5)</p><p>Page %d of 3</p><form method="POST" action="%s"><input type="hidden" name="pn" value="%d"></form>`, index, html.EscapeString(board), index)
				for id := 2*index - 1; id <= min(5, 2*index); id++ {
					if mode == "njoyn-short-tail" && index == 3 {
						break
					}
					fmt.Fprintf(w, `<a href="/corp/xweb/xweb.asp?clid=21001&amp;page=JobDetails&amp;jobid=%d&amp;brid=1">Engineer</a>`, id)
				}
				io.WriteString(w, `</body></html>`)
			}))
			address := "127.0.0.2"
			if runtime.GOOS == "darwin" {
				address = "127.0.0.1"
			}
			listener, e := net.Listen("tcp4", address+":0")
			if e != nil {
				t.Fatal(e)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxyAt(t, origin, address, host)
			execution := &runtimeV1ServiceExecution{dayforceConfig: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dayforceRun: func(ctx context.Context, c Config, task Task) (Result, error) {
				result, err := runTaskWithDependencies(ctx, c, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
				if mode == "brass-cleanup" && err == nil {
					return Result{}, errCleanupUnproved
				}
				if err != nil {
					t.Logf("owned fixture failure: %T %v", err, err)
				}
				return result, err
			}}
			ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
			defer cancel()
			response, e := execution.executeAPIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: board, Provider: provider, Metadata: json.RawMessage(metadata), TimeoutMS: 30000})
			if e != nil {
				t.Fatal(e)
			}
			want := "success"
			if strings.Contains(mode, "publisher") {
				want = "publisher_reserved"
			}
			if mode == "brass-duplicate" || mode == "brass-changed-total" || mode == "njoyn-short-tail" || mode == "brass-cleanup" {
				want = "failed"
			}
			if response.Outcome != want {
				mu.Lock()
				t.Log("fixture requests", requests)
				mu.Unlock()
				t.Fatal("physical session outcome", response.Outcome, want)
			}
			if want != "success" {
				if len(response.Inventory) > 0 {
					t.Fatal("unsuccessful session published prefix")
				}
				return
			}
			var inventory api.Inventory
			if json.Unmarshal(response.Inventory, &inventory) != nil || len(inventory.Jobs) != 5 || inventory.Truncated || inventory.URLOnly != (provider == "njoyn") || strings.Contains(string(response.Inventory), "private-session-cookie") {
				t.Fatal("inventory/count/privacy")
			}
			if provider == "brassring" && (inventory.Jobs[2].URL != board+"&jobid=3" || fmt.Sprint(inventory.Jobs[2].Locations) != "[Basel]" || inventory.Jobs[0].DatePosted != "2026-10-13") {
				t.Fatal("hydrated canonical fields")
			}
			mu.Lock()
			defer mu.Unlock()
			posts := 0
			for _, r := range requests {
				if strings.HasPrefix(r, "POST ") {
					posts++
				}
			}
			wantPosts := 4
			if mode == "brass-recover" {
				wantPosts = 8
			}
			if posts != wantPosts {
				t.Fatal("page conservation / two complete passes", posts)
			}
		})
	}
}
