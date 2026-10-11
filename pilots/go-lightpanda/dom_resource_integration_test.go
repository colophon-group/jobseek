package main

import (
	"context"
	"io"
	"net/http"
	"os"
	"runtime"
	"strings"
	"sync"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

// The original Chromium auto/false route blocks font and media, but renders
// this script/fetch-produced job. Keep that engine assumption in the existing
// pinned physical integration gate, including on future Lightpanda upgrades.
func TestLightpandaDOMAutoResourceIntegration(t *testing.T) {
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("pinned Lightpanda physical integration requires Linux")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expected); err != nil {
		t.Fatal(err)
	}
	var mu sync.Mutex
	hits := map[string]int{}
	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		hits[r.URL.Path]++
		mu.Unlock()
		body, kind := "fixture", "application/octet-stream"
		switch r.URL.Path {
		case "/":
			kind = "text/html"
			body = `<!doctype html><html><head><link rel="stylesheet" href="/style.css"><script defer src="/app.js"></script></head><body><div style="font-family:Probe">Font</div><img src="/image.png"><audio autoplay preload="auto" src="/media.mp3"></audio><video autoplay preload="auto" src="/media.mp4"></video></body></html>`
			w.Header().Set("TDM-Reservation", "0")
			w.Header().Set("TDM-Policy", "https://fixture.invalid/license")
		case "/style.css":
			kind, body = "text/css", `@font-face {font-family:Probe;src:url('/font.woff2')}`
		case "/app.js":
			kind = "application/javascript"
			body = `fetch('/data.json').then(r=>r.json()).then(j=>{document.body.insertAdjacentHTML('beforeend','<a class=job href=/jobs/1>Job</a>');if(document.fonts)document.fonts.load('12px Probe').catch(()=>{});});`
		case "/data.json":
			kind, body = "application/json", `{"ready":true}`
		}
		w.Header().Set("Content-Type", kind)
		_, _ = io.WriteString(w, body)
	}))
	adapter, err := lightpandaadapter.NewNavigationRenderOnly(runtimeV1Runner{
		config: Config{Binary: binary, EgressPolicy: defaultEgressPolicy()},
		run:    testOnlyFixtureRunner(binary, "127.0.0.2"),
	})
	if err != nil {
		t.Fatal(err)
	}
	input := bridgeInput(origin.URL+"/", "", 0)
	input.Plan.Navigation.TimeoutMs = 10000
	input.Plan.Navigation.WaitUntil = runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE
	result := adapter.Execute(context.Background(), input)
	success := result.GetSuccess()
	if success == nil || success.GetStatus() != 200 || success.FinalUrl != origin.URL+"/" ||
		!strings.Contains(string(bridgeManifestBody(success.Html)), "/jobs/1") ||
		success.ResourcePolicy.GetTdmReservationHeader() != "0" ||
		success.ResourcePolicy.GetTdmPolicyHeader() != "https://fixture.invalid/license" {
		t.Fatal("original lean job or publisher signals changed", result)
	}
	mu.Lock()
	defer mu.Unlock()
	if hits["/"] != 1 || hits["/app.js"] != 1 || hits["/data.json"] != 1 {
		t.Fatal("script-produced inventory incomplete", hits)
	}
	for _, path := range []string{"/font.woff2", "/media.mp3", "/media.mp4"} {
		if hits[path] != 0 {
			t.Fatal("engine default no longer preserves original lean exclusions", hits)
		}
	}
}
