package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// testOnlyFixtureStarter is compiled only into the Go test binary. It allows
// one exact non-CDP loopback address so hermetic positive fixtures remain
// reachable while the rest of 127/8 stays denied. Production constructors have
// no equivalent field, option, environment variable, or request input.
type testOnlyFixtureStarter struct {
	binary      string
	fixtureAddr netip.Addr
}

func (starter testOnlyFixtureStarter) Start(port int) (managedProcess, error) {
	if !starter.fixtureAddr.Is4() || !starter.fixtureAddr.IsLoopback() || starter.fixtureAddr.String() == "127.0.0.1" {
		return nil, errors.New("test fixture address must be an exact non-CDP IPv4 loopback address")
	}
	logs := &boundedBuffer{limit: maxProcessLogBytes}
	blockCIDRs := baselineBlockedCIDRs + ",-" + starter.fixtureAddr.String() + "/32"
	command := exec.Command(starter.binary, fixedLightpandaServeArgs(port, blockCIDRs)...)
	command.Env = append([]string(nil), lightpandaChildEnvironment...)
	command.SysProcAttr = lightpandaProcessAttributes()
	command.Stdout = logs
	command.Stderr = logs
	if err := command.Start(); err != nil {
		return nil, err
	}
	return &commandProcess{command: command, pgid: command.Process.Pid, logs: logs}, nil
}

func testOnlyFixtureRunner(binary, fixtureIP string) taskRunner {
	fixtureAddr := netip.MustParseAddr(fixtureIP)
	return func(ctx context.Context, config Config, task Task) (Result, error) {
		config.Binary = binary
		config.EgressPolicy = defaultEgressPolicy()
		return runTaskWithDependencies(ctx, config, dependencies{
			process:      testOnlyFixtureStarter{binary: binary, fixtureAddr: fixtureAddr},
			ready:        httpReadyWaiter{interval: defaultReadyInterval},
			executor:     chromedpExecutor{},
			allocatePort: allocateLoopbackPort,
			releasePort:  releaseLoopbackPort,
			portOpen:     loopbackPortOpen,
		}, task)
	}
}

func TestFixtureStarterCannotExemptCDPLoopback(t *testing.T) {
	starter := testOnlyFixtureStarter{binary: "/nonexistent/lightpanda", fixtureAddr: netip.MustParseAddr("127.0.0.1")}
	if _, err := starter.Start(9222); err == nil {
		t.Fatal("test-only fixture starter accepted the CDP loopback address")
	}
}

func newTestLoopbackServer(t *testing.T, ip string, handler http.Handler) *httptest.Server {
	t.Helper()
	listener, err := net.Listen("tcp4", net.JoinHostPort(ip, "0"))
	if err != nil {
		t.Fatalf("listen on test loopback %s: %v", ip, err)
	}
	server := httptest.NewUnstartedServer(handler)
	server.Listener = listener
	server.Start()
	t.Cleanup(server.Close)
	return server
}

func TestLightpandaEgressPolicyIntegration(t *testing.T) {
	binary := integrationBinary(t)

	forbiddenHits := &requestPathCounts{}
	forbidden := newTestLoopbackServer(t, "127.0.0.3", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		forbiddenHits.add(request.URL.Path)
		writer.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(writer, "forbidden")
	}))

	t.Run("default direct navigation", func(t *testing.T) {
		forbiddenHits.reset()
		_, err := runTask(context.Background(), Config{Binary: binary, EgressPolicy: defaultEgressPolicy()}, Task{URL: forbidden.URL + "/direct"})
		if err == nil {
			t.Fatal("default policy allowed direct loopback navigation")
		}
		if hits := forbiddenHits.snapshot(); len(hits) != 0 {
			t.Fatalf("forbidden direct-navigation sink received requests: %v", hits)
		}
	})

	t.Run("redirect", func(t *testing.T) {
		forbiddenHits.reset()
		origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			http.Redirect(writer, request, forbidden.URL+"/redirected", http.StatusFound)
		}))
		_, err := testOnlyFixtureRunner(binary, "127.0.0.2")(
			context.Background(),
			Config{EgressPolicy: defaultEgressPolicy()},
			Task{URL: origin.URL + "/redirect"},
		)
		if err == nil {
			t.Fatal("fixture policy followed a redirect to a forbidden loopback address")
		}
		if hits := forbiddenHits.snapshot(); len(hits) != 0 {
			t.Fatalf("forbidden redirect sink received requests: %v", hits)
		}
	})

	t.Run("subresource fetch xhr websocket and CDP-shaped loopback", func(t *testing.T) {
		forbiddenHits.reset()
		cdpDecoyHits := atomic.Int64{}
		cdpDecoy := newTestLoopbackServer(t, "127.0.0.1", http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
			cdpDecoyHits.Add(1)
			writer.Header().Set("Content-Type", "application/json")
			_, _ = io.WriteString(writer, `{"webSocketDebuggerUrl":"ws://127.0.0.1:1/devtools/browser/decoy"}`)
		}))

		origin := newTestLoopbackServer(t, "127.0.0.2", http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
			if request.URL.Path == "/delay.js" {
				time.Sleep(300 * time.Millisecond)
				writer.Header().Set("Content-Type", "text/javascript")
				_, _ = io.WriteString(writer, `document.documentElement.dataset.delay = "done";`)
				return
			}
			forbiddenBase := forbidden.URL
			cdpDecoyURL := cdpDecoy.URL + "/json/version"
			writer.Header().Set("Content-Type", "text/html; charset=utf-8")
			_, _ = fmt.Fprintf(writer, `<!doctype html><html><head>
<script async src="%s/subresource.js" onerror="document.documentElement.dataset.subresource = 'blocked'" onload="document.documentElement.dataset.subresource = 'loaded'"></script>
<script>
try {
  fetch(%q).catch(function () {});
  document.documentElement.dataset.fetchCall = "returned";
} catch (error) { document.documentElement.dataset.fetchCall = "threw"; }
try {
  var xhr = new XMLHttpRequest(); xhr.open("GET", %q); xhr.send();
  document.documentElement.dataset.xhrSend = "returned";
} catch (error) { document.documentElement.dataset.xhrSend = "threw"; }
try {
  var socket = new WebSocket(%q.replace("http:", "ws:"));
  socket.onerror = function () {};
  document.documentElement.dataset.websocket = "constructed";
} catch (error) { document.documentElement.dataset.websocket = "threw"; }
try {
  fetch(%q).catch(function () {});
  document.documentElement.dataset.cdpFetch = "returned";
} catch (error) { document.documentElement.dataset.cdpFetch = "threw"; }
</script>
<script src="/delay.js"></script>
</head><body>egress fixture</body></html>`, forbiddenBase, forbiddenBase+"/fetch", forbiddenBase+"/xhr", forbiddenBase+"/websocket", cdpDecoyURL)
		}))

		result, err := testOnlyFixtureRunner(binary, "127.0.0.2")(
			context.Background(),
			Config{EgressPolicy: defaultEgressPolicy()},
			Task{URL: origin.URL + "/page"},
		)
		if err != nil {
			t.Fatalf("allowed fixture navigation failed: %v", err)
		}
		for _, marker := range []string{
			`data-subresource="blocked"`,
			`data-fetch-call="returned"`,
			`data-xhr-send="returned"`,
			`data-websocket="constructed"`,
			`data-cdp-fetch="returned"`,
			`data-delay="done"`,
		} {
			if !strings.Contains(result.HTML, marker) {
				t.Fatalf("network API marker %q is missing: %q", marker, result.HTML)
			}
		}
		if hits := forbiddenHits.snapshot(); len(hits) != 0 {
			t.Fatalf("forbidden subresource/fetch/XHR/WebSocket sink received requests: %v", hits)
		}
		if hits := cdpDecoyHits.Load(); hits != 0 {
			t.Fatalf("CDP-shaped 127.0.0.1 sink received %d page requests", hits)
		}
	})
}

type requestPathCounts struct {
	mu     sync.Mutex
	counts map[string]int
}

func (counts *requestPathCounts) add(path string) {
	counts.mu.Lock()
	defer counts.mu.Unlock()
	if counts.counts == nil {
		counts.counts = make(map[string]int)
	}
	counts.counts[path]++
}

func (counts *requestPathCounts) reset() {
	counts.mu.Lock()
	defer counts.mu.Unlock()
	counts.counts = make(map[string]int)
}

func (counts *requestPathCounts) snapshot() map[string]int {
	counts.mu.Lock()
	defer counts.mu.Unlock()
	result := make(map[string]int, len(counts.counts))
	for path, count := range counts.counts {
		result[path] = count
	}
	return result
}

func integrationBinary(t *testing.T) string {
	t.Helper()
	expectedSHA256, supported := lightpandaStable040SHA256[runtime.GOARCH]
	if runtime.GOOS != "linux" || !supported {
		t.Skip("stable Lightpanda 0.4.0 integration binary requires Linux amd64 or arm64")
	}
	binary := os.Getenv("LIGHTPANDA_INTEGRATION_BIN")
	if binary == "" {
		t.Skip("set LIGHTPANDA_INTEGRATION_BIN to opt in")
	}
	if err := verifyFileSHA256(binary, expectedSHA256); err != nil {
		t.Fatal(err)
	}
	return binary
}
