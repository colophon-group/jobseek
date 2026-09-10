package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/chromedp"
)

const (
	defaultTaskTimeout    = 20 * time.Second
	defaultReadyInterval  = 25 * time.Millisecond
	defaultCleanupTimeout = 2 * time.Second
	defaultTerminateGrace = 500 * time.Millisecond
	maxURLBytes           = 8 << 10
	maxExpressionBytes    = 16 << 10
	maxHTMLBytes          = 512 << 10
	maxExpressionResult   = 64 << 10
	maxProcessLogBytes    = 32 << 10
)

var lightpandaChildEnvironment = []string{
	"LIGHTPANDA_DISABLE_CORE_DUMP=1",
	"LIGHTPANDA_DISABLE_TELEMETRY=true",
}

var (
	errCleanupUnproved = errors.New("lightpanda cleanup could not be proved")
	errResourceLimit   = errors.New("lightpanda result exceeded a resource limit")
	loopbackPorts      = struct {
		sync.Mutex
		reserved map[int]struct{}
	}{reserved: make(map[int]struct{})}
)

// Task is intentionally small: the pilot navigates once and optionally
// evaluates one synchronous JavaScript expression.
type Task struct {
	URL        string
	Evaluation *TaskEvaluation
}

// TaskEvaluation is present exactly for B1 work. Presence is explicit so an
// empty or malformed B1 expression can never be mistaken for render-only B0.
type TaskEvaluation struct {
	Expression     string
	MaxResultBytes int
}

// Result contains only data from the top-level document.
type Result struct {
	Status      int             `json:"status"`
	FinalURL    string          `json:"final_url"`
	HTML        string          `json:"html"`
	HTMLPresent bool            `json:"-"`
	Expression  json.RawMessage `json:"expression"`
}

type managedProcess interface {
	Wait() error
	SignalGroup(syscall.Signal) error
	GroupAlive() (bool, error)
	Logs() string
}

type processStarter interface {
	Start(port int) (managedProcess, error)
}

type readyWaiter interface {
	Wait(context.Context, int, *processState) (string, error)
}

type taskExecutor interface {
	Execute(context.Context, string, Task) (Result, error)
}

type dependencies struct {
	process      processStarter
	ready        readyWaiter
	executor     taskExecutor
	allocatePort func() (int, error)
	releasePort  func(int)
	portOpen     func(int) bool
}

type Config struct {
	Binary         string
	TaskTimeout    time.Duration
	CleanupTimeout time.Duration
	TerminateGrace time.Duration
}

func normalizeConfig(config Config) Config {
	if config.Binary == "" {
		config.Binary = "lightpanda"
	}
	if config.TaskTimeout <= 0 {
		config.TaskTimeout = defaultTaskTimeout
	}
	if config.CleanupTimeout <= 0 {
		config.CleanupTimeout = defaultCleanupTimeout
	}
	if config.TerminateGrace <= 0 {
		config.TerminateGrace = defaultTerminateGrace
	}
	return config
}

func runTask(ctx context.Context, config Config, task Task) (Result, error) {
	config = normalizeConfig(config)
	return runTaskWithDependencies(ctx, config, dependencies{
		process:      commandStarter{binary: config.Binary},
		ready:        httpReadyWaiter{interval: defaultReadyInterval},
		executor:     chromedpExecutor{},
		allocatePort: allocateLoopbackPort,
		releasePort:  releaseLoopbackPort,
		portOpen:     loopbackPortOpen,
	}, task)
}

func runTaskWithDependencies(ctx context.Context, config Config, deps dependencies, task Task) (Result, error) {
	if err := validateTask(task); err != nil {
		return Result{}, err
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}

	port, err := deps.allocatePort()
	if err != nil {
		return Result{}, fmt.Errorf("allocate loopback port: %w", err)
	}
	process, err := deps.process.Start(port)
	if err != nil {
		if deps.releasePort != nil {
			deps.releasePort(port)
		}
		return Result{}, fmt.Errorf("start lightpanda: %w", err)
	}
	state := watchProcess(process)

	taskCtx, cancel := context.WithTimeout(ctx, config.TaskTimeout)
	result, runErr := executeTaskSafely(taskCtx, deps, process, state, port, task)
	cancel()

	cleanupErr := cleanupProcess(config, deps, process, state, port)
	if cleanupErr != nil {
		runErr = errors.Join(
			runErr,
			errCleanupUnproved,
			fmt.Errorf("cleanup lightpanda: %w", cleanupErr),
		)
	} else if deps.releasePort != nil {
		deps.releasePort(port)
	}
	if runErr != nil {
		return Result{}, runErr
	}
	return result, nil
}

func executeTaskSafely(
	ctx context.Context,
	deps dependencies,
	process managedProcess,
	state *processState,
	port int,
	task Task,
) (result Result, err error) {
	defer func() {
		if recover() != nil {
			result = Result{}
			err = errors.New("lightpanda execution panicked")
		}
	}()
	return executeTask(ctx, deps, process, state, port, task)
}

func executeTask(ctx context.Context, deps dependencies, process managedProcess, state *processState, port int, task Task) (Result, error) {
	cdpURL, err := deps.ready.Wait(ctx, port, state)
	if err != nil {
		logs := strings.TrimSpace(process.Logs())
		if logs != "" {
			err = fmt.Errorf("%w (lightpanda: %s)", err, logs)
		}
		return Result{}, fmt.Errorf("wait for lightpanda readiness: %w", err)
	}
	if err := validateCDPEndpoint(cdpURL, port); err != nil {
		return Result{}, err
	}
	result, err := deps.executor.Execute(ctx, cdpURL, task)
	if err != nil {
		return Result{}, fmt.Errorf("execute CDP task: %w", err)
	}
	if err := validateResult(task, result); err != nil {
		return Result{}, err
	}
	return result, nil
}

func validateTask(task Task) error {
	if len(task.URL) == 0 || len(task.URL) > maxURLBytes {
		return fmt.Errorf("URL must contain 1..%d bytes", maxURLBytes)
	}
	parsed, err := url.Parse(task.URL)
	if err != nil || parsed.Host == "" || parsed.User != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return errors.New("URL must be an absolute http or https URL")
	}
	if task.Evaluation == nil {
		return nil
	}
	if len(task.Evaluation.Expression) == 0 || len(task.Evaluation.Expression) > maxExpressionBytes {
		return fmt.Errorf("expression must contain 1..%d bytes", maxExpressionBytes)
	}
	if task.Evaluation.MaxResultBytes <= 0 || task.Evaluation.MaxResultBytes > maxExpressionResult {
		return fmt.Errorf("expression result limit must contain 1..%d bytes", maxExpressionResult)
	}
	return nil
}

func validateCDPEndpoint(raw string, expectedPort int) error {
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("invalid CDP WebSocket URL: %w", err)
	}
	if parsed.Scheme != "ws" {
		return errors.New("CDP endpoint must use ws")
	}
	if parsed.User != nil || parsed.Hostname() == "" || parsed.Port() == "" {
		return errors.New("CDP endpoint must have an unauthenticated host and port")
	}
	if parsed.Hostname() != "127.0.0.1" {
		return fmt.Errorf("CDP endpoint must use literal host 127.0.0.1, got %q", parsed.Hostname())
	}
	port, err := strconv.Atoi(parsed.Port())
	if err != nil || port < 1 || port > 65535 {
		return errors.New("CDP endpoint must have a valid TCP port")
	}
	if port != expectedPort {
		return fmt.Errorf("CDP endpoint port %d does not match allocated port %d", port, expectedPort)
	}
	expectedAuthority := net.JoinHostPort("127.0.0.1", strconv.Itoa(expectedPort))
	if parsed.Host != expectedAuthority {
		return fmt.Errorf("CDP endpoint authority must be exactly %q", expectedAuthority)
	}
	return nil
}

func validateResult(task Task, result Result) error {
	if result.Status < 100 || result.Status > 599 {
		return fmt.Errorf("missing or invalid main-document response status %d", result.Status)
	}
	if len(result.FinalURL) == 0 || len(result.FinalURL) > maxURLBytes {
		return errors.New("final URL is missing or too large")
	}
	if !result.HTMLPresent {
		return errors.New("main-document outerHTML is missing")
	}
	if len(result.HTML) > maxHTMLBytes {
		return fmt.Errorf("%w: main-document outerHTML exceeds %d bytes", errResourceLimit, maxHTMLBytes)
	}
	if task.Evaluation == nil {
		if result.Expression != nil {
			return errors.New("render-only task returned an expression result")
		}
		return nil
	}
	if len(result.Expression) == 0 {
		return errors.New("expression result is empty")
	}
	if len(result.Expression) > task.Evaluation.MaxResultBytes {
		return fmt.Errorf("%w: expression result exceeds %d bytes", errResourceLimit, task.Evaluation.MaxResultBytes)
	}
	if !json.Valid(result.Expression) {
		return errors.New("expression result is not valid JSON")
	}
	return nil
}

type processState struct {
	done chan struct{}
	mu   sync.Mutex
	err  error
}

func watchProcess(process managedProcess) *processState {
	state := &processState{done: make(chan struct{})}
	go func() {
		var err error
		func() {
			defer func() {
				if recover() != nil {
					err = errors.New("lightpanda process wait panicked")
				}
			}()
			err = process.Wait()
		}()
		state.mu.Lock()
		state.err = err
		state.mu.Unlock()
		close(state.done)
	}()
	return state
}

func (s *processState) waitErr() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.err
}

func (s *processState) readinessError() error {
	select {
	case <-s.done:
		if err := s.waitErr(); err != nil {
			return fmt.Errorf("lightpanda exited before readiness: %w", err)
		}
		return errors.New("lightpanda exited before readiness")
	default:
		return nil
	}
}

func cleanupProcess(config Config, deps dependencies, process managedProcess, state *processState, port int) error {
	deadline := time.Now().Add(config.CleanupTimeout)
	var errs []error
	if err := signalProcessGroup(process, syscall.SIGTERM); err != nil {
		errs = append(errs, fmt.Errorf("terminate process group: %w", err))
	}

	grace := config.TerminateGrace
	if remaining := time.Until(deadline); grace > remaining {
		grace = remaining
	}
	if !waitUntil(state.done, grace) {
		alive, err := processGroupAlive(process)
		if err != nil {
			errs = append(errs, fmt.Errorf("verify process group before kill: %w", err))
		} else if alive {
			if err := signalProcessGroup(process, syscall.SIGKILL); err != nil {
				errs = append(errs, fmt.Errorf("kill process group: %w", err))
			}
		}
	}

	if !waitUntil(state.done, time.Until(deadline)) {
		errs = append(errs, errors.New("process was not reaped before cleanup deadline"))
	}

	// The leader may have exited while descendants retained its process group.
	// Check before signaling so an already-gone group is never killed solely
	// because its former numeric PGID was retained in commandProcess.
	alive, err := processGroupAlive(process)
	if err != nil {
		errs = append(errs, fmt.Errorf("verify residual process group: %w", err))
	} else if alive {
		if err := signalProcessGroup(process, syscall.SIGKILL); err != nil {
			errs = append(errs, fmt.Errorf("kill residual process group: %w", err))
		}
	}

	for {
		alive, err := processGroupAlive(process)
		if err != nil {
			errs = append(errs, fmt.Errorf("verify process group: %w", err))
			break
		}
		if !alive {
			portOpen, portErr := portOpenSafely(deps.portOpen, port)
			if portErr != nil {
				errs = append(errs, fmt.Errorf("verify CDP listener: %w", portErr))
				break
			}
			if !portOpen {
				return errors.Join(errs...)
			}
		}
		if time.Now().After(deadline) {
			if alive {
				errs = append(errs, errors.New("process group still exists after cleanup"))
			}
			portOpen, portErr := portOpenSafely(deps.portOpen, port)
			if portErr != nil {
				errs = append(errs, fmt.Errorf("verify CDP listener after cleanup deadline: %w", portErr))
				return errors.Join(errs...)
			}
			if portOpen {
				errs = append(errs, errors.New("CDP listener still accepts connections after cleanup"))
			}
			return errors.Join(errs...)
		}
		time.Sleep(10 * time.Millisecond)
	}
	return errors.Join(errs...)
}

func signalProcessGroup(process managedProcess, signal syscall.Signal) (err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("lightpanda process signal panicked")
		}
	}()
	return process.SignalGroup(signal)
}

func processGroupAlive(process managedProcess) (alive bool, err error) {
	defer func() {
		if recover() != nil {
			alive = false
			err = errors.New("lightpanda process-group check panicked")
		}
	}()
	return process.GroupAlive()
}

func portOpenSafely(check func(int) bool, port int) (open bool, err error) {
	defer func() {
		if recover() != nil {
			open = false
			err = errors.New("lightpanda listener check panicked")
		}
	}()
	return check(port), nil
}

func waitUntil(done <-chan struct{}, timeout time.Duration) bool {
	if timeout <= 0 {
		select {
		case <-done:
			return true
		default:
			return false
		}
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-done:
		return true
	case <-timer.C:
		return false
	}
}

type commandStarter struct {
	binary string
}

func (s commandStarter) Start(port int) (managedProcess, error) {
	logs := &boundedBuffer{limit: maxProcessLogBytes}
	command := s.buildCommand(port, logs)
	if err := command.Start(); err != nil {
		return nil, err
	}
	return &commandProcess{command: command, pgid: command.Process.Pid, logs: logs}, nil
}

func (s commandStarter) buildCommand(port int, logs io.Writer) *exec.Cmd {
	command := exec.Command(s.binary,
		"serve",
		"--host", "127.0.0.1",
		"--port", strconv.Itoa(port),
		"--log-level", "error",
	)
	command.Env = append([]string(nil), lightpandaChildEnvironment...)
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	command.Stdout = logs
	command.Stderr = logs
	return command
}

type commandProcess struct {
	command *exec.Cmd
	pgid    int
	logs    *boundedBuffer
}

func (p *commandProcess) Wait() error {
	return p.command.Wait()
}

func (p *commandProcess) SignalGroup(signal syscall.Signal) error {
	err := syscall.Kill(-p.pgid, signal)
	if errors.Is(err, syscall.ESRCH) {
		return nil
	}
	return err
}

func (p *commandProcess) GroupAlive() (bool, error) {
	err := syscall.Kill(-p.pgid, 0)
	switch {
	case err == nil, errors.Is(err, syscall.EPERM):
		return true, nil
	case errors.Is(err, syscall.ESRCH):
		return false, nil
	default:
		return false, err
	}
}

func (p *commandProcess) Logs() string {
	return p.logs.String()
}

type boundedBuffer struct {
	mu    sync.Mutex
	data  []byte
	limit int
}

func (b *boundedBuffer) Write(data []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	remaining := b.limit - len(b.data)
	if remaining > 0 {
		if remaining > len(data) {
			remaining = len(data)
		}
		b.data = append(b.data, data[:remaining]...)
	}
	return len(data), nil
}

func (b *boundedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return string(bytes.Clone(b.data))
}

func allocateLoopbackPort() (int, error) {
	return allocateLoopbackPortWithListener(func() (net.Listener, error) {
		return net.Listen("tcp4", "127.0.0.1:0")
	})
}

func allocateLoopbackPortWithListener(listen func() (net.Listener, error)) (int, error) {
	for {
		listener, err := listen()
		if err != nil {
			return 0, err
		}
		port := listener.Addr().(*net.TCPAddr).Port

		loopbackPorts.Lock()
		_, alreadyReserved := loopbackPorts.reserved[port]
		if !alreadyReserved {
			loopbackPorts.reserved[port] = struct{}{}
		}
		loopbackPorts.Unlock()

		if err := listener.Close(); err != nil {
			if !alreadyReserved {
				releaseLoopbackPort(port)
			}
			return 0, err
		}
		if !alreadyReserved {
			return port, nil
		}
	}
}

// releaseLoopbackPort ends the ownership established by allocateLoopbackPort.
// The kernel listener must be closed before Lightpanda can bind, so this
// process-local reservation prevents a concurrent sibling task from receiving
// the same close-then-bind port without serializing unrelated executions.
func releaseLoopbackPort(port int) {
	loopbackPorts.Lock()
	delete(loopbackPorts.reserved, port)
	loopbackPorts.Unlock()
}

func loopbackPortOpen(port int) bool {
	connection, err := net.DialTimeout("tcp4", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), 25*time.Millisecond)
	if err != nil {
		return false
	}
	_ = connection.Close()
	return true
}

type httpReadyWaiter struct {
	interval time.Duration
}

func (w httpReadyWaiter) Wait(ctx context.Context, port int, state *processState) (string, error) {
	endpoint := "http://" + net.JoinHostPort("127.0.0.1", strconv.Itoa(port)) + "/json/version"
	client := &http.Client{
		Timeout: 200 * time.Millisecond,
		Transport: &http.Transport{
			Proxy:             nil,
			DisableKeepAlives: true,
		},
	}
	ticker := time.NewTicker(w.interval)
	defer ticker.Stop()

	for {
		if err := state.readinessError(); err != nil {
			return "", err
		}
		webSocketURL, ready, err := fetchVersion(ctx, client, endpoint)
		if err == nil && ready {
			if err := validateCDPEndpoint(webSocketURL, port); err != nil {
				return "", err
			}
			if err := state.readinessError(); err != nil {
				return "", err
			}
			return webSocketURL, nil
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-state.done:
			return "", state.readinessError()
		case <-ticker.C:
		}
	}
}

func fetchVersion(ctx context.Context, client *http.Client, endpoint string) (string, bool, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return "", false, err
	}
	response, err := client.Do(request)
	if err != nil {
		return "", false, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4<<10))
		return "", false, fmt.Errorf("version endpoint returned %s", response.Status)
	}
	var version struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, 64<<10))
	if err := decoder.Decode(&version); err != nil {
		return "", false, err
	}
	if version.WebSocketDebuggerURL == "" {
		return "", false, errors.New("version response omitted webSocketDebuggerUrl")
	}
	return version.WebSocketDebuggerURL, true, nil
}

type chromedpExecutor struct{}

type mainDocumentResponse struct {
	status   int64
	url      string
	loaderID cdp.LoaderID
}

type mainDocumentFrame struct {
	url      string
	loaderID cdp.LoaderID
}

func correlateMainDocumentSnapshot(response mainDocumentResponse, frame mainDocumentFrame, finalURL string) (int64, error) {
	if response.loaderID == "" || frame.loaderID == "" {
		return 0, errors.New("main-document loader correlation is unavailable")
	}
	if response.loaderID != frame.loaderID {
		return 0, errors.New("main-document response does not match the captured frame loader")
	}
	responseURL, err := comparableDocumentURL(response.url)
	if err != nil {
		return 0, fmt.Errorf("invalid main-document response URL: %w", err)
	}
	frameURL, err := comparableDocumentURL(frame.url)
	if err != nil {
		return 0, fmt.Errorf("invalid captured frame URL: %w", err)
	}
	capturedURL, err := comparableDocumentURL(finalURL)
	if err != nil {
		return 0, fmt.Errorf("invalid captured final URL: %w", err)
	}
	if responseURL != frameURL || capturedURL != frameURL {
		return 0, errors.New("main-document response URL does not match the captured document")
	}
	return response.status, nil
}

func comparableDocumentURL(raw string) (string, error) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Host == "" || parsed.User != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return "", errors.New("URL must be an absolute unauthenticated http or https URL")
	}
	parsed.Fragment = ""
	return parsed.String(), nil
}

func (chromedpExecutor) Execute(ctx context.Context, cdpURL string, task Task) (Result, error) {
	allocatorCtx, cancelAllocator := chromedp.NewRemoteAllocator(ctx, cdpURL, chromedp.NoModifyURL)
	defer cancelAllocator()
	targetCtx, cancelTarget := chromedp.NewContext(allocatorCtx)
	defer cancelTarget()

	var mainFrame cdp.FrameID
	var latestResponse mainDocumentResponse
	var responseMu sync.Mutex

	if err := chromedp.Run(targetCtx,
		network.Enable(),
		page.Enable(),
		chromedp.ActionFunc(func(actionCtx context.Context) error {
			frameTree, err := page.GetFrameTree().Do(actionCtx)
			if err != nil {
				return err
			}
			mainFrame = frameTree.Frame.ID
			return nil
		}),
	); err != nil {
		return Result{}, fmt.Errorf("initialize fresh target: %w", err)
	}
	chromedp.ListenTarget(targetCtx, func(event any) {
		eventResponse, ok := event.(*network.EventResponseReceived)
		if !ok || eventResponse.Response == nil || eventResponse.Type != network.ResourceTypeDocument || eventResponse.FrameID != mainFrame {
			return
		}
		responseMu.Lock()
		latestResponse = mainDocumentResponse{
			status:   eventResponse.Response.Status,
			url:      eventResponse.Response.URL,
			loaderID: eventResponse.LoaderID,
		}
		responseMu.Unlock()
	})

	var finalURL string
	var html string
	var capturedFrame mainDocumentFrame
	if err := chromedp.Run(targetCtx,
		chromedp.Navigate(task.URL),
		chromedp.WaitReady("html", chromedp.ByQuery),
		chromedp.Location(&finalURL),
		chromedp.OuterHTML("html", &html, chromedp.ByQuery),
		chromedp.ActionFunc(func(actionCtx context.Context) error {
			frameTree, err := page.GetFrameTree().Do(actionCtx)
			if err != nil {
				return err
			}
			if frameTree.Frame.ID != mainFrame {
				return errors.New("main frame changed while capturing navigation result")
			}
			capturedFrame = mainDocumentFrame{
				url:      frameTree.Frame.URL,
				loaderID: frameTree.Frame.LoaderID,
			}
			return nil
		}),
	); err != nil {
		return Result{}, err
	}

	// Freeze the navigation result before evaluating caller JavaScript. Even a
	// synchronous expression can initiate another main-frame navigation.
	responseMu.Lock()
	capturedResponse := latestResponse
	responseMu.Unlock()
	mainStatus, err := correlateMainDocumentSnapshot(capturedResponse, capturedFrame, finalURL)
	if err != nil {
		return Result{}, err
	}

	var expression json.RawMessage
	if task.Evaluation != nil {
		if err := chromedp.Run(targetCtx,
			chromedp.ActionFunc(func(actionCtx context.Context) error {
				remoteObject, exception, err := runtime.Evaluate(task.Evaluation.Expression).
					WithReturnByValue(true).
					WithAwaitPromise(false).
					Do(actionCtx)
				if err != nil {
					return err
				}
				if exception != nil {
					return fmt.Errorf("expression evaluation: %w", exception)
				}
				expression, err = serializeExpressionResult(remoteObject)
				return err
			}),
		); err != nil {
			return Result{}, err
		}
	}

	return Result{
		Status:      int(mainStatus),
		FinalURL:    finalURL,
		HTML:        html,
		HTMLPresent: true,
		Expression:  expression,
	}, nil
}

func serializeExpressionResult(remoteObject *runtime.RemoteObject) (json.RawMessage, error) {
	if remoteObject == nil {
		return nil, errors.New("expression returned no remote object")
	}
	if remoteObject.Type != runtime.TypeObject && remoteObject.Subtype != "" {
		return nil, fmt.Errorf("expression returned subtype %q for non-object type %q", remoteObject.Subtype, remoteObject.Type)
	}
	if remoteObject.Subtype == runtime.SubtypePromise {
		return nil, errors.New("expression returned a Promise; only synchronous expressions are supported")
	}
	if remoteObject.UnserializableValue != "" {
		return nil, fmt.Errorf("expression returned non-JSON value %q", remoteObject.UnserializableValue)
	}
	switch remoteObject.Type {
	case runtime.TypeUndefined, runtime.TypeFunction, runtime.TypeSymbol, runtime.TypeBigint, runtime.TypeAccessor:
		return nil, fmt.Errorf("expression returned non-JSON type %q", remoteObject.Type)
	case runtime.TypeObject, runtime.TypeString, runtime.TypeNumber, runtime.TypeBoolean:
	default:
		return nil, fmt.Errorf("expression returned unsupported type %q", remoteObject.Type)
	}
	if remoteObject.Subtype == runtime.SubtypeNull {
		if len(remoteObject.Value) == 0 || bytes.Equal(remoteObject.Value, []byte("null")) {
			return json.RawMessage("null"), nil
		}
		return nil, errors.New("expression returned inconsistent null value")
	}
	if len(remoteObject.Value) == 0 {
		return nil, fmt.Errorf("expression returned no JSON value for type %q", remoteObject.Type)
	}
	value := bytes.Clone(remoteObject.Value)
	if !json.Valid(value) {
		return nil, errors.New("expression returned invalid JSON")
	}
	return value, nil
}
