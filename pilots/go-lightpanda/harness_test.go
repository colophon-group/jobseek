package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/runtime"
)

func TestValidateCDPEndpoint(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		url     string
		wantErr bool
	}{
		{name: "IPv4", url: "ws://127.0.0.1:9222/devtools/browser/id"},
		{name: "noncanonical port", url: "ws://127.0.0.1:09222/devtools/browser/id", wantErr: true},
		{name: "IPv6", url: "ws://[::1]:9222/devtools/browser/id", wantErr: true},
		{name: "localhost", url: "ws://localhost:9222/devtools/browser/id", wantErr: true},
		{name: "wrong port", url: "ws://127.0.0.1:9333/devtools/browser/id", wantErr: true},
		{name: "public IP", url: "ws://203.0.113.9:9222/devtools/browser/id", wantErr: true},
		{name: "DNS name", url: "ws://browser.example:9222/devtools/browser/id", wantErr: true},
		{name: "credentials", url: "ws://user@127.0.0.1:9222/devtools/browser/id", wantErr: true},
		{name: "TLS", url: "wss://127.0.0.1:9222/devtools/browser/id", wantErr: true},
		{name: "HTTP", url: "http://127.0.0.1:9222/json/version", wantErr: true},
		{name: "missing port", url: "ws://127.0.0.1/devtools/browser/id", wantErr: true},
		{name: "bad port", url: "ws://127.0.0.1:not-a-port/devtools/browser/id", wantErr: true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			err := validateCDPEndpoint(test.url, 9222)
			if (err != nil) != test.wantErr {
				t.Fatalf("validateCDPEndpoint(%q) error = %v, wantErr %v", test.url, err, test.wantErr)
			}
		})
	}
}

func TestRunTaskRejectsWrongCDPEndpointAndStillCleansUp(t *testing.T) {
	events := &eventLog{}
	process := newFakeProcess(events, true)
	executed := false
	config, deps := testRunner(process, taskExecutorFunc(func(context.Context, string, Task) (Result, error) {
		executed = true
		return validResult(), nil
	}), func(int) bool {
		events.add("verify-listener")
		return false
	})
	deps.ready = readyWaiterFunc(func(context.Context, int, *processState) (string, error) {
		return "ws://198.51.100.4:9222/devtools/browser/test", nil
	})

	if _, err := runTaskWithDependencies(context.Background(), config, deps, validTask()); err == nil {
		t.Fatal("Run accepted a non-loopback CDP endpoint")
	}
	if executed {
		t.Fatal("task executor ran for a non-loopback CDP endpoint")
	}
	assertOrdered(t, events.snapshot(), "signal-15", "wait-reaped", "verify-listener")
}

func TestRunTaskCleansUpAfterTaskTimeout(t *testing.T) {
	events := &eventLog{}
	process := newFakeProcess(events, false)
	config, deps := testRunner(process, taskExecutorFunc(func(ctx context.Context, _ string, _ Task) (Result, error) {
		events.add("execute")
		<-ctx.Done()
		events.add("executor-canceled")
		return Result{}, ctx.Err()
	}), func(int) bool {
		events.add("verify-listener")
		return false
	})
	config.TaskTimeout = 20 * time.Millisecond

	_, err := runTaskWithDependencies(context.Background(), config, deps, validTask())
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Run error = %v, want deadline exceeded", err)
	}
	assertOrdered(t, events.snapshot(), "executor-canceled", "signal-15", "wait-reaped", "verify-listener")
}

func TestRunTaskFailsAfterUnverifiedCleanup(t *testing.T) {
	events := &eventLog{}
	process := newFakeProcess(events, true)
	config, deps := testRunner(process, taskExecutorFunc(func(context.Context, string, Task) (Result, error) {
		return validResult(), nil
	}), func(int) bool { return true })
	config.CleanupTimeout = 20 * time.Millisecond
	config.TerminateGrace = 5 * time.Millisecond

	if _, err := runTaskWithDependencies(context.Background(), config, deps, validTask()); err == nil {
		t.Fatal("Run succeeded despite listener cleanup failure")
	}
	if contains(events.snapshot(), "signal-9") {
		t.Fatal("cleanup sent SIGKILL after the process group was already gone")
	}
}

func TestReadyWaiterChecksChildBeforeRequest(t *testing.T) {
	state := exitedProcessState(errors.New("start failure"))
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	_, err := (httpReadyWaiter{interval: time.Millisecond}).Wait(ctx, 1, state)
	if err == nil || !errors.Is(err, state.waitErr()) {
		t.Fatalf("Wait error = %v, want process exit error", err)
	}
}

func TestReadyWaiterChecksChildAfterResponse(t *testing.T) {
	state := &processState{done: make(chan struct{})}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		state.mu.Lock()
		state.err = errors.New("exited during readiness")
		state.mu.Unlock()
		close(state.done)
		_ = json.NewEncoder(writer).Encode(map[string]string{
			"webSocketDebuggerUrl": "ws://" + request.Host + "/devtools/browser/test",
		})
	}))
	defer server.Close()
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	_, err = (httpReadyWaiter{interval: time.Millisecond}).Wait(ctx, port, state)
	if err == nil || !errors.Is(err, state.waitErr()) {
		t.Fatalf("Wait error = %v, want process exit error", err)
	}
}

func TestReadyWaiterRejectsMismatchedResponsePort(t *testing.T) {
	state := &processState{done: make(chan struct{})}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		_ = json.NewEncoder(writer).Encode(map[string]string{
			"webSocketDebuggerUrl": "ws://127.0.0.1:1/devtools/browser/test",
		})
	}))
	defer server.Close()
	parsed, err := url.Parse(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if _, err := (httpReadyWaiter{interval: time.Millisecond}).Wait(ctx, port, state); err == nil {
		t.Fatal("Wait accepted a WebSocket URL on a different port")
	}
}

func TestResultBounds(t *testing.T) {
	t.Parallel()
	result := validResult()
	result.HTML = string(make([]byte, maxHTMLBytes+1))
	if err := validateResult(result); err == nil {
		t.Fatal("validateResult accepted oversized HTML")
	}
	result = validResult()
	result.Expression = nil
	if err := validateResult(result); err == nil {
		t.Fatal("validateResult accepted an empty expression result")
	}
	result = validResult()
	result.Expression = json.RawMessage(`{"not":"closed"`)
	if err := validateResult(result); err == nil {
		t.Fatal("validateResult accepted invalid expression JSON")
	}
}

func TestSerializeExpressionResult(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		object  *runtime.RemoteObject
		want    string
		wantErr bool
	}{
		{name: "string", object: &runtime.RemoteObject{Type: runtime.TypeString, Value: []byte(`"Jobs"`)}, want: `"Jobs"`},
		{name: "object", object: &runtime.RemoteObject{Type: runtime.TypeObject, Value: []byte(`{"jobs":1}`)}, want: `{"jobs":1}`},
		{name: "null value", object: &runtime.RemoteObject{Type: runtime.TypeObject, Subtype: runtime.SubtypeNull, Value: []byte(`null`)}, want: `null`},
		{name: "null without value", object: &runtime.RemoteObject{Type: runtime.TypeObject, Subtype: runtime.SubtypeNull}, want: `null`},
		{name: "nil object", wantErr: true},
		{name: "undefined", object: &runtime.RemoteObject{Type: runtime.TypeUndefined}, wantErr: true},
		{name: "function", object: &runtime.RemoteObject{Type: runtime.TypeFunction}, wantErr: true},
		{name: "symbol", object: &runtime.RemoteObject{Type: runtime.TypeSymbol}, wantErr: true},
		{name: "promise", object: &runtime.RemoteObject{Type: runtime.TypeObject, Subtype: runtime.SubtypePromise}, wantErr: true},
		{name: "unserializable number", object: &runtime.RemoteObject{Type: runtime.TypeNumber, UnserializableValue: runtime.UnserializableValue("NaN")}, wantErr: true},
		{name: "missing object value", object: &runtime.RemoteObject{Type: runtime.TypeObject}, wantErr: true},
		{name: "invalid JSON", object: &runtime.RemoteObject{Type: runtime.TypeObject, Value: []byte(`{"jobs":`)}, wantErr: true},
		{name: "inconsistent null", object: &runtime.RemoteObject{Type: runtime.TypeObject, Subtype: runtime.SubtypeNull, Value: []byte(`{}`)}, wantErr: true},
		{name: "primitive null subtype", object: &runtime.RemoteObject{Type: runtime.TypeString, Subtype: runtime.SubtypeNull, Value: []byte(`null`)}, wantErr: true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			got, err := serializeExpressionResult(test.object)
			if (err != nil) != test.wantErr {
				t.Fatalf("serializeExpressionResult() error = %v, wantErr %v", err, test.wantErr)
			}
			if string(got) != test.want {
				t.Fatalf("serializeExpressionResult() = %q, want %q", got, test.want)
			}
		})
	}
}

func TestCorrelateMainDocumentSnapshot(t *testing.T) {
	t.Parallel()
	validResponse := mainDocumentResponse{
		status:   http.StatusCreated,
		url:      "https://example.test/jobs",
		loaderID: cdp.LoaderID("loader-a"),
	}
	validFrame := mainDocumentFrame{
		url:      "https://example.test/jobs#frame-fragment",
		loaderID: cdp.LoaderID("loader-a"),
	}
	tests := []struct {
		name      string
		response  mainDocumentResponse
		frame     mainDocumentFrame
		finalURL  string
		wantError bool
	}{
		{name: "matching loader and document", response: validResponse, frame: validFrame, finalURL: "https://example.test/jobs#final-fragment"},
		{name: "missing response loader", response: mainDocumentResponse{status: http.StatusCreated, url: validResponse.url}, frame: validFrame, finalURL: validFrame.url, wantError: true},
		{name: "different loader", response: mainDocumentResponse{status: http.StatusCreated, url: validResponse.url, loaderID: cdp.LoaderID("loader-b")}, frame: validFrame, finalURL: validFrame.url, wantError: true},
		{name: "different response URL", response: mainDocumentResponse{status: http.StatusCreated, url: "https://example.test/other", loaderID: validResponse.loaderID}, frame: validFrame, finalURL: validFrame.url, wantError: true},
		{name: "different captured URL", response: validResponse, frame: validFrame, finalURL: "https://example.test/other", wantError: true},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			status, err := correlateMainDocumentSnapshot(test.response, test.frame, test.finalURL)
			if (err != nil) != test.wantError {
				t.Fatalf("correlateMainDocumentSnapshot() error = %v, wantError %v", err, test.wantError)
			}
			if !test.wantError && status != http.StatusCreated {
				t.Fatalf("status = %d, want %d", status, http.StatusCreated)
			}
		})
	}
}

func testRunner(process *fakeProcess, executor taskExecutor, portOpen func(int) bool) (Config, dependencies) {
	config := Config{
		TaskTimeout:    time.Second,
		CleanupTimeout: 500 * time.Millisecond,
		TerminateGrace: 100 * time.Millisecond,
	}
	deps := dependencies{
		process: &fixedStarter{process: process},
		ready: readyWaiterFunc(func(context.Context, int, *processState) (string, error) {
			return "ws://127.0.0.1:9222/devtools/browser/test", nil
		}),
		executor:     executor,
		allocatePort: func() (int, error) { return 9222, nil },
		portOpen:     portOpen,
	}
	return config, deps
}

func validTask() Task {
	return Task{URL: "https://example.test/jobs", Expression: "document.title"}
}

func validResult() Result {
	return Result{Status: 200, FinalURL: "https://example.test/jobs", HTML: "<html></html>", Expression: json.RawMessage(`"Jobs"`)}
}

type readyWaiterFunc func(context.Context, int, *processState) (string, error)

func (function readyWaiterFunc) Wait(ctx context.Context, port int, state *processState) (string, error) {
	return function(ctx, port, state)
}

type taskExecutorFunc func(context.Context, string, Task) (Result, error)

func (function taskExecutorFunc) Execute(ctx context.Context, cdpURL string, task Task) (Result, error) {
	return function(ctx, cdpURL, task)
}

type fixedStarter struct {
	process managedProcess
}

func (starter *fixedStarter) Start(int) (managedProcess, error) {
	return starter.process, nil
}

type fakeProcess struct {
	events     *eventLog
	exitOnTerm bool
	done       chan struct{}
	once       sync.Once
	mu         sync.Mutex
	alive      bool
}

func newFakeProcess(events *eventLog, exitOnTerm bool) *fakeProcess {
	return &fakeProcess{
		events:     events,
		exitOnTerm: exitOnTerm,
		done:       make(chan struct{}),
		alive:      true,
	}
}

func (process *fakeProcess) Wait() error {
	<-process.done
	process.events.add("wait-reaped")
	return nil
}

func (process *fakeProcess) SignalGroup(signal syscall.Signal) error {
	process.events.add(fmt.Sprintf("signal-%d", signal))
	if signal == syscall.SIGKILL || (signal == syscall.SIGTERM && process.exitOnTerm) {
		process.once.Do(func() {
			process.mu.Lock()
			process.alive = false
			process.mu.Unlock()
			close(process.done)
		})
	}
	return nil
}

func (process *fakeProcess) GroupAlive() (bool, error) {
	process.events.add("verify-group")
	process.mu.Lock()
	defer process.mu.Unlock()
	return process.alive, nil
}

func (*fakeProcess) Logs() string { return "" }

type eventLog struct {
	mu     sync.Mutex
	events []string
}

func (log *eventLog) add(event string) {
	log.mu.Lock()
	defer log.mu.Unlock()
	log.events = append(log.events, event)
}

func (log *eventLog) snapshot() []string {
	log.mu.Lock()
	defer log.mu.Unlock()
	return append([]string(nil), log.events...)
}

func assertOrdered(t *testing.T, events []string, wanted ...string) {
	t.Helper()
	position := 0
	for _, event := range events {
		if position < len(wanted) && event == wanted[position] {
			position++
		}
	}
	if position != len(wanted) {
		t.Fatalf("events %v do not contain ordered sequence %v", events, wanted)
	}
}

func contains(events []string, wanted string) bool {
	for _, event := range events {
		if event == wanted {
			return true
		}
	}
	return false
}

func exitedProcessState(err error) *processState {
	state := &processState{done: make(chan struct{}), err: err}
	close(state.done)
	return state
}
