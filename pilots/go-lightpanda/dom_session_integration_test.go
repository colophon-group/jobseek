package main

import (
	"context"
	"net/http"
	"net/netip"
	"os"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

// The production URL validator remains unchanged. Like the existing feed/CDP
// fixture, only this test executor maps the admitted task onto its disposable
// HTTP loopback origin after task validation. No production route uses it.
type domSessionFixtureExecutor struct{ root string }

func (e domSessionFixtureExecutor) Execute(ctx context.Context, endpoint string, task Task) (Result, error) {
	task.URL = e.root
	copyFeed := *task.Feed
	copyFeed.request.FeedURL = e.root
	task.Feed = &copyFeed
	return (chromedpExecutor{}).Execute(ctx, endpoint, task)
}

func TestLightpandaDOMSessionRendersRootFetchesRawTailAndRetainsCookies(t *testing.T) {
	expected, supported := lightpandaPinnedSHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("pinned physical Lightpanda fixture requires Linux")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN")
	}
	if e := verifyFileSHA256(binary, expected); e != nil {
		t.Fatal(e)
	}
	var roots, tails, third atomic.Int64
	const tail = `<html><body><a href="/job/tail">Tail Engineer</a><script>document.cookie="root-script=changed; path=/"</script></body></html>`
	origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		if r.URL.RawQuery == "" {
			roots.Add(1)
			http.SetCookie(w, &http.Cookie{Name: "root-session", Value: "held", Path: "/"})
			_, _ = w.Write([]byte(`<html><body><a href="/job/root">Root</a><script>document.cookie="root-script=held; path=/"</script></body></html>`))
			return
		}
		for _, name := range []string{"root-session", "root-script"} {
			c, e := r.Cookie(name)
			if e != nil || c.Value != "held" {
				t.Error("same-target cookie lost or raw tail executed")
				w.WriteHeader(403)
				return
			}
		}
		if r.URL.Query().Get("page") == "2" {
			if tails.Add(1) == 1 {
				w.WriteHeader(503)
				return
			}
			w.Header().Set("TDM-Reservation", "0")
			w.Header().Set("TDM-Policy", "https://fixture.invalid/license")
			_, _ = w.Write([]byte(tail))
			return
		}
		third.Add(1)
		_, _ = w.Write([]byte(`<p>End</p>`))
	}))
	request := feed.Request{Protocol: feed.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), FeedURL: "https://fixture.example/jobs", Start: 1, Increment: 1, MaxPages: 3, Wait: "domcontentloaded", NavigationTimeoutMS: 10000, TimeoutMS: 30000, DOM: &feed.DOMTraversal{Parameter: "page", PageStart: 1, Increment: 1, Attempts: 2}}
	task := Task{URL: request.FeedURL, ResponseBodyLimit: feed.BodyLimit, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: 10 * time.Second}, Feed: &feedTask{request: request, converse: func(ctx context.Context, fetch feedPageFetch) (Result, error) {
		root, e := fetch(ctx, origin.URL+"/jobs")
		if e != nil || !strings.Contains(string(root.ResponseBody), "/job/root") {
			t.Error("rendered root missing")
			return Result{}, feed.ErrProtocol
		}
		raw, e := fetch(ctx, origin.URL+"/jobs?page=2")
		if e != nil || string(raw.ResponseBody) != tail || raw.ResourcePolicy.GetTdmPolicyHeader() != "https://fixture.invalid/license" {
			t.Error("raw tail/status/header provenance lost", e)
			return Result{}, feed.ErrProtocol
		}
		end, e := fetch(ctx, origin.URL+"/jobs?page=3")
		if e != nil || end.Status != 200 {
			t.Error("same target changed", e)
			return Result{}, feed.ErrProtocol
		}
		return Result{feedSessionSettled: true}, nil
	}}}
	result, e := runTaskWithDependencies(context.Background(), Config{Binary: binary, TaskTimeout: 30 * time.Second, EgressPolicy: defaultEgressPolicy()}, dependencies{process: testOnlyFixtureStarter{binary: binary, fixtureAddr: netip.MustParseAddr("127.0.0.2")}, ready: httpReadyWaiter{interval: defaultReadyInterval}, executor: domSessionFixtureExecutor{origin.URL + "/jobs"}, allocatePort: allocateLoopbackPort, releasePort: releaseLoopbackPort, portOpen: loopbackPortOpen}, task)
	if e != nil || !result.feedSessionSettled || roots.Load() != 1 || tails.Load() != 2 || third.Load() != 1 {
		t.Fatal("physical DOM traversal/cleanup differs", e, roots.Load(), tails.Load(), third.Load())
	}
}
