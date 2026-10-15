//go:build !densitybench

package main

import (
	"context"
	"crypto/tls"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
)

func interactionIntegrationBinary(t *testing.T) string {
	t.Helper()
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("requires pinned physical integration binary")
	}
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS == "darwin" && runtime.GOARCH == "arm64" {
		expected, supported = "d4b66ac8484f49d7a49e05b6f9342716c747129527fcbe8ffe36679ffa18dd60", true
	} else if runtime.GOOS != "linux" {
		supported = false
	}
	if !supported || verifyFileSHA256(binary, expected) != nil {
		t.Fatal("official physical release identity differs")
	}
	return binary
}

// A navigation replaces the JS context. Keep the controller outside the page,
// conserve every page and stop before a reserved subsequent page. The fixture
// uses the existing test-only transport and never contacts a public publisher.
func TestLightpandaDocumentInteractionsIntegration(t *testing.T) {
	binary := interactionIntegrationBinary(t)
	address := "127.0.0.2"
	if runtime.GOOS == "darwin" {
		address = "127.0.0.1"
	}
	for _, mode := range []string{"navigation", "later-publisher", "no-progress", "cap", "repeat", "visible-repeat", "visible-publisher", "has-text-click", "mouse-down-click", "delegated-click", "wait-attached-detached", "wait-visible-hidden", "wait-first-hidden", "missing-required", "missing-optional", "page-size", "history-transition"} {
		t.Run(mode, func(t *testing.T) {
			certificate, ca := dayforceOriginTLS(t)
			var mu sync.Mutex
			requests := []string{}
			origin := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				mu.Lock()
				requests = append(requests, r.URL.Path)
				mu.Unlock()
				w.Header().Set("Content-Type", "text/html")
				if mode == "later-publisher" && r.URL.Path == "/page/2" {
					w.Header().Set("Tdm-Reservation", "1")
				}
				html := `<a href="/jobs/one">One</a>`
				switch mode {
				case "navigation", "later-publisher", "no-progress", "cap":
					switch r.URL.Path {
					case "/page/1":
						html += `<a class="next" href="/page/2">Next</a>`
					case "/page/2":
						if mode != "no-progress" {
							html = `<a href="/jobs/two">Two</a>`
						}
						if mode == "later-publisher" || mode == "cap" {
							html += `<a class="next" href="/page/3">Next</a>`
						}
					default:
						html = `<a href="/jobs/three">Three</a>`
					}
				case "repeat":
					html += `<button id="more" onclick="window.n=(window.n||0)+1;const a=document.createElement('a');a.href='/jobs/added'+window.n;document.body.appendChild(a);if(window.n===2)this.remove()">More</button>`
				case "visible-repeat", "visible-publisher":
					html += `<button class="more" style="visibility:hidden;width:80px;height:24px" onclick="document.body.dataset.bad='1'">Hidden</button><button class="more" style="display:none;width:80px;height:24px" onclick="document.body.dataset.bad='1'">None</button><button class="more" style="width:0;height:0;padding:0;border:0;font-size:0" onclick="document.body.dataset.bad='1'"></button>`
					policy := ""
					if mode == "visible-publisher" {
						policy = `const m=document.createElement('meta');m.name='tdm-reservation';m.content='1';document.head.appendChild(m);`
					}
					// Opacity zero still satisfies the original visibility rule.
					html += `<button class="more" style="opacity:0;width:80px;height:24px" onclick="window.n=(window.n||0)+1;const a=document.createElement('a');a.href='/jobs/added'+window.n;document.body.appendChild(a);` + policy + `if(window.n===2)this.remove()">More</button>`
				case "has-text-click":
					html += `<button onclick="document.body.dataset.wrong='1'">Other</button><button onclick="document.body.dataset.clicked='1'">  Load   MORE </button>`
				case "mouse-down-click":
					html += `<button id="dropdown" onmousedown="this.dataset.down='1'" onclick="if(this.dataset.entered==='1'&&this.dataset.down==='1')document.body.dataset.opened='1'">Open</button><script>document.querySelector('#dropdown').addEventListener('mouseover',function(){this.dataset.entered='1'})</script>`
				case "delegated-click":
					html += `<ul id="menu"><li id="item">50</li></ul><script>document.querySelector("#menu").addEventListener("click",e=>{if(e.target.id==="item")document.body.dataset.delegated="1"})</script>`
				case "history-transition":
					html += `<button id="history" onclick="history.replaceState({},'', '/page/1?page=2');document.body.dataset.history='1'">Next</button>`
				case "wait-attached-detached":
					html += `<span id="old">Old</span><script>setTimeout(()=>{document.querySelector('#old').remove();const n=document.createElement('span');n.id='new';document.body.appendChild(n)},20)</script>`
				case "wait-visible-hidden":
					html += `<div id="appear" style="display:none;width:80px;height:24px;opacity:0">Role</div><div id="disappear" style="width:80px;height:24px">Old</div><script>setTimeout(()=>{document.querySelector('#appear').style.display='block';document.querySelector('#disappear').style.visibility='hidden';document.body.dataset.waited='1'},350)</script>`
				case "wait-first-hidden":
					html += `<div class="choice" style="display:none;width:80px;height:24px">First hidden</div><div class="choice" style="width:80px;height:24px">Second visible</div>`
				case "page-size":
					html += `<select id="size" onchange="const a=document.createElement('a');a.href='/jobs/size'+this.value;document.body.appendChild(a)"><option>10</option><option>100</option></select>`
				}
				_, _ = io.WriteString(w, `<!doctype html><html><head></head><body>`+html+`</body></html>`)
			}))
			listener, err := net.Listen("tcp4", address+":0")
			if err != nil {
				t.Fatal(err)
			}
			origin.Listener = listener
			origin.TLS = &tls.Config{Certificates: []tls.Certificate{certificate}, MinVersion: tls.VersionTLS12}
			origin.StartTLS()
			defer origin.Close()
			proxy := dayforceConnectProxyAt(t, origin, address)
			pipeline := []actions.Action{{Kind: "paginate_collect", NextSelector: "a.next", MaxPages: 5, WaitMS: 5, TimeoutMS: 5000, Required: true}}
			switch mode {
			case "cap":
				pipeline[0].MaxPages = 1
			case "repeat":
				pipeline = []actions.Action{{Kind: "repeat", Selector: "#more", Maximum: 5, WaitMS: 5, TimeoutMS: 5000, Required: true}}
			case "visible-repeat", "visible-publisher":
				pipeline = []actions.Action{{Kind: "repeat", Selector: ".more:visible", Maximum: 5, WaitMS: 5, TimeoutMS: 5000, Required: true}}
				if mode == "visible-publisher" {
					pipeline = append(pipeline, actions.Action{Kind: "evaluate", Script: `()=>document.body.dataset.late='1'`, TimeoutMS: 1000, Required: true})
				}
			case "has-text-click":
				pipeline = []actions.Action{{Kind: "click", Selector: `button:has-text("load more")`, TimeoutMS: 5000, Required: true}}
			case "mouse-down-click":
				pipeline = []actions.Action{{Kind: "click", Selector: "#dropdown", TimeoutMS: 5000, Required: true}}
			case "delegated-click":
				pipeline = []actions.Action{{Kind: "click", Selector: "#item", TimeoutMS: 5000, Required: true}}
			case "history-transition":
				pipeline = []actions.Action{{Kind: "click", Selector: "#history", TimeoutMS: 5000, Required: true}}
			case "wait-attached-detached":
				pipeline = []actions.Action{{Kind: "wait_for", Selector: "#new", State: "attached", TimeoutMS: 5000, Required: true}, {Kind: "wait_for", Selector: "#old", State: "detached", TimeoutMS: 5000, Required: true}}
			case "wait-visible-hidden":
				pipeline = []actions.Action{{Kind: "wait_for", Selector: "#appear", State: "visible", TimeoutMS: 5000, Required: true}, {Kind: "wait_for", Selector: "#disappear", State: "hidden", TimeoutMS: 5000, Required: true}, {Kind: "wait_for", Selector: "#missing", State: "hidden", TimeoutMS: 5000, Required: true}}
			case "wait-first-hidden":
				pipeline = []actions.Action{{Kind: "wait_for", Selector: ".choice", State: "visible", TimeoutMS: 500, Required: true}}
			case "missing-required", "missing-optional":
				pipeline = []actions.Action{{Kind: "click", Selector: "#missing", TimeoutMS: 500, Required: mode == "missing-required"}, {Kind: "evaluate", Script: `()=>document.body.dataset.continued='1'`, TimeoutMS: 1000, Required: true}}
			case "page-size":
				pipeline[0].PageSizeSelector = "#size"
				pipeline[0].PageSize = "100"
			}
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			task := Task{URL: "https://jobs.dayforcehcm.com/page/1", Actions: pipeline}
			result, err := runTaskWithDependencies(ctx, Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, dependencies{process: dayforceFixtureStarter{binary: binary, proxy: proxy.URL, ca: ca, fixtureLoopback: address}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: chromedpExecutor{egressPolicy: defaultEgressPolicy()}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
			if mode == "no-progress" || mode == "cap" || mode == "missing-required" || mode == "wait-first-hidden" {
				if err == nil {
					t.Fatal("incomplete interaction published a document")
				}
				return
			}
			if err != nil {
				t.Fatal("physical interaction failed", err)
			}
			html := result.HTML
			switch mode {
			case "wait-visible-hidden":
				if !strings.Contains(html, `data-waited="1"`) {
					t.Fatal("visibility wait returned before original state")
				}
			case "history-transition":
				if result.FinalURL != "https://jobs.dayforcehcm.com/page/1?page=2" || !strings.Contains(html, `data-history="1"`) || result.Status != 200 {
					t.Fatal("witnessed history transition lost document provenance")
				}
			case "navigation":
				if !strings.Contains(html, "/jobs/one") || !strings.Contains(html, "/jobs/two") {
					t.Fatal("navigation lost an earlier inventory")
				}
			case "later-publisher":
				mu.Lock()
				defer mu.Unlock()
				for _, path := range requests {
					if path == "/page/3" {
						t.Fatal("collector browsed past publisher reservation")
					}
				}
				if !strings.Contains(html, "/jobs/two") || strings.Contains(html, "/jobs/three") || result.ResourcePolicy.GetTdmReservationHeader() != "1" {
					t.Fatal("reserved final document identity lost")
				}
			case "repeat":
				if !strings.Contains(html, "/jobs/added1") || !strings.Contains(html, "/jobs/added2") {
					t.Fatal("repeat stopped before link growth terminated")
				}
			case "visible-repeat":
				if !strings.Contains(html, "/jobs/added1") || !strings.Contains(html, "/jobs/added2") || strings.Contains(html, `data-bad="1"`) {
					t.Fatal("visibility lost eligible opacity-zero controls or clicked hidden/zero-size controls")
				}
			case "visible-publisher":
				if !strings.Contains(html, "/jobs/added1") || strings.Contains(html, "/jobs/added2") || strings.Contains(html, `data-late="1"`) || !strings.Contains(html, `name="tdm-reservation"`) {
					t.Fatal("visible interaction continued past publisher reservation")
				}
			case "has-text-click":
				if !strings.Contains(html, `data-clicked="1"`) || strings.Contains(html, `data-wrong="1"`) {
					t.Fatal("quoted text selector changed first matching target")
				}
			case "mouse-down-click":
				if !strings.Contains(html, `data-opened="1"`) {
					t.Fatal("click lost the mouse-down dropdown transition")
				}
			case "delegated-click":
				if !strings.Contains(html, `data-delegated="1"`) {
					t.Fatal("click did not reach ancestor handler")
				}
			case "wait-attached-detached":
				if !strings.Contains(html, `id="new"`) || strings.Contains(html, `id="old"`) {
					t.Fatal("attachment wait returned before replacement")
				}
			case "missing-optional":
				if !strings.Contains(html, `data-continued="1"`) {
					t.Fatal("optional missing click prevented continuation")
				}
			case "page-size":
				if !strings.Contains(html, "/jobs/size100") {
					t.Fatal("page size change event lost")
				}
			}
		})
	}
}
