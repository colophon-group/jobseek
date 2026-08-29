package harness

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/chromedp/cdproto/fetch"
	"github.com/chromedp/cdproto/network"
	"github.com/chromedp/chromedp"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

const (
	FixtureOrigin = "http://fixture:8080"
	LightpandaWS  = "ws://lightpanda:9222"
)

type Runner struct {
	WSURL         string
	AllowedOrigin string
	Limits        Limits
}

type executionState struct {
	mu              sync.Mutex
	allowedOrigin   string
	robots          RobotsPolicy
	limits          Limits
	ledger          []LedgerEntry
	requestIndex    map[network.RequestID]int
	requestCount    uint64
	responseBytes   uint64
	actualBytes     uint64
	completed       map[network.RequestID]struct{}
	mainStatus      int64
	mainTDMHeader   string
	failure         *runtimev1.BrowserResult
	cancelExecution context.CancelFunc
}

type eventBarrier struct {
	done chan struct{}
}

func (r Runner) Execute(parent context.Context, plan *runtimev1.BrowserPlan) (*runtimev1.BrowserResult, []LedgerEntry, uint64, uint64, Cleanup) {
	cleanup := Cleanup{TargetsBefore: -1, TargetsAfter: -1, Outcome: "not-started"}
	if r.WSURL != LightpandaWS || r.AllowedOrigin != FixtureOrigin {
		result := Failure(runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY, "probe endpoints must use the fixed internal services")
		return result, nil, 0, 0, cleanup
	}
	if r.Limits.MaxRequests == 0 || r.Limits.MaxResponseBytes == 0 {
		result := Failure(runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY, "request and response-byte limits must be positive")
		return result, nil, 0, 0, cleanup
	}
	if typed, err := ValidatePlan(plan, r.AllowedOrigin); err != nil {
		result := Failure(runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY, err.Error())
		return result, nil, 0, 0, cleanup
	} else if typed != nil {
		return typed, nil, 0, 0, cleanup
	}

	state := &executionState{
		allowedOrigin: r.AllowedOrigin,
		limits:        r.Limits,
		requestIndex:  make(map[network.RequestID]int),
		completed:     make(map[network.RequestID]struct{}),
	}
	if failure := state.loadRobots(parent); failure != nil {
		ledger, requests, bytes := state.snapshot()
		return failure, ledger, requests, bytes, cleanup
	}

	allocatorCtx, allocatorCancel := chromedp.NewRemoteAllocator(parent, r.WSURL, chromedp.NoModifyURL)
	defer allocatorCancel()
	tabCtx, tabCancel := chromedp.NewContext(allocatorCtx)
	executionCtx, executionCancel := context.WithCancel(tabCtx)
	state.cancelExecution = executionCancel
	defer executionCancel()

	timeout := 5 * time.Second
	if navigation := plan.GetNavigation(); navigation != nil && navigation.GetTimeoutMs() > 0 {
		timeout = time.Duration(navigation.GetTimeoutMs()) * time.Millisecond
	}
	executionCtx, timeoutCancel := context.WithTimeout(executionCtx, timeout)
	defer timeoutCancel()

	events := make(chan any, 64)
	workerDone := make(chan struct{})
	chromedp.ListenTarget(executionCtx, func(event any) {
		switch event.(type) {
		case *fetch.EventRequestPaused, *network.EventResponseReceived, *network.EventLoadingFinished:
			select {
			case events <- event:
			case <-executionCtx.Done():
			}
		}
	})
	go func() {
		defer close(workerDone)
		for {
			select {
			case event := <-events:
				if barrier, ok := event.(eventBarrier); ok {
					close(barrier.done)
					continue
				}
				state.handleEvent(executionCtx, event)
			case <-executionCtx.Done():
				return
			}
		}
	}()

	pattern := &fetch.RequestPattern{URLPattern: "*", RequestStage: fetch.RequestStageRequest}
	setupErr := chromedp.Run(executionCtx,
		network.Enable(),
		fetch.Enable().WithPatterns([]*fetch.RequestPattern{pattern}),
	)
	if setupErr != nil {
		executionCancel()
		<-workerDone
		tabCancel()
		cleanup.SessionClosed = true
		cleanup.Outcome = "closed-after-setup-error"
		result := classifyExecutionError(setupErr, false)
		ledger, requests, bytes := state.snapshot()
		return result, ledger, requests, bytes, cleanup
	}
	if targets, err := chromedp.Targets(executionCtx); err == nil {
		cleanup.TargetsBefore = len(targets)
	}

	runErr := chromedp.Run(executionCtx,
		chromedp.Navigate(plan.GetTargetUrl()),
		chromedp.WaitReady("body", chromedp.ByQuery),
	)
	var html, finalURL string
	var evaluations []*runtimev1.EvaluationValue
	if runErr == nil && state.getFailure() == nil {
		runErr = chromedp.Run(executionCtx,
			chromedp.OuterHTML("html", &html, chromedp.ByQuery),
			chromedp.Location(&finalURL),
		)
	}
	if runErr == nil && state.getFailure() == nil {
		for _, evaluation := range plan.GetEvaluations() {
			var value any
			if err := chromedp.Run(executionCtx, chromedp.Evaluate(evaluation.GetExpression(), &value)); err != nil {
				runErr = err
				break
			}
			encoded, err := json.Marshal(value)
			if err != nil {
				runErr = err
				break
			}
			maxBytes := evaluation.GetMaxResultBytes()
			if maxBytes == 0 {
				maxBytes = 4096
			}
			if uint64(len(encoded)) > maxBytes {
				state.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "evaluation result exceeded its byte limit"))
				break
			}
			evaluations = append(evaluations, &runtimev1.EvaluationValue{
				EvaluationId: evaluation.GetEvaluationId(),
				Value:        EvaluationEnvelope(encoded),
			})
		}
	}
	if runErr == nil && state.getFailure() == nil {
		barrier := eventBarrier{done: make(chan struct{})}
		select {
		case events <- barrier:
			select {
			case <-barrier.done:
			case <-executionCtx.Done():
			}
		case <-executionCtx.Done():
		}
	}

	if targets, err := chromedp.Targets(executionCtx); err == nil {
		cleanup.TargetsAfter = len(targets)
	}
	executionCancel()
	<-workerDone
	tabCancel()
	cleanup.SessionClosed = true
	cleanup.Outcome = "closed"

	if failure := state.getFailure(); failure != nil {
		ledger, requests, bytes := state.snapshot()
		return failure, ledger, requests, bytes, cleanup
	}
	if runErr != nil {
		result := classifyExecutionError(runErr, true)
		ledger, requests, bytes := state.snapshot()
		return result, ledger, requests, bytes, cleanup
	}
	if TDMReserved(state.getMainTDMHeader(), html) {
		result := Failure(runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic document reserves text and data mining")
		ledger, requests, bytes := state.snapshot()
		return result, ledger, requests, bytes, cleanup
	}
	status := uint32(state.getMainStatus())
	result := newResult(&runtimev1.BrowserSuccess{
		FinalUrl:    finalURL,
		Status:      &status,
		Html:        InlineManifest([]byte(html)),
		Evaluations: evaluations,
	})
	ledger, requests, bytes := state.snapshot()
	return result, ledger, requests, bytes, cleanup
}

func (s *executionState) loadRobots(ctx context.Context) *runtimev1.BrowserResult {
	transport := &http.Transport{
		Proxy: nil,
		DialContext: (&net.Dialer{
			Timeout: 2 * time.Second,
		}).DialContext,
		DisableKeepAlives: true,
	}
	client := &http.Client{
		Transport: transport,
		Timeout:   3 * time.Second,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return errors.New("robots redirect rejected")
		},
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, s.allowedOrigin+"/robots.txt", nil)
	if err != nil {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "could not construct robots request")
	}
	response, err := client.Do(request)
	if err != nil {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic robots policy is unavailable")
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, int64(s.limits.MaxResponseBytes)+1))
	entry := LedgerEntry{Method: http.MethodGet, Path: "/robots.txt", ResourceType: "Policy", Decision: "allowed", Status: int64(response.StatusCode), ResponseBytes: uint64(len(body))}
	s.mu.Lock()
	s.ledger = append(s.ledger, entry)
	s.requestCount++
	s.responseBytes += uint64(len(body))
	s.actualBytes += uint64(len(body))
	s.mu.Unlock()
	if err != nil || response.StatusCode != http.StatusOK {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_NAVIGATION, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic robots policy could not be read")
	}
	if s.requestCount > s.limits.MaxRequests || s.responseBytes > s.limits.MaxResponseBytes {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "robots policy exceeded probe limits")
	}
	if TDMReserved(response.Header.Get("TDM-Reservation"), "") {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic robots response reserves text and data mining")
	}
	s.robots = ParseRobots(string(body))
	return nil
}

func (s *executionState) handleEvent(ctx context.Context, event any) {
	switch value := event.(type) {
	case *fetch.EventRequestPaused:
		s.handleRequestPaused(ctx, value)
	case *network.EventResponseReceived:
		s.handleResponse(value)
	case *network.EventLoadingFinished:
		s.handleLoadingFinished(value)
	}
}

func (s *executionState) handleRequestPaused(ctx context.Context, event *fetch.EventRequestPaused) {
	path, reason := s.authorizeURL(event.Request.URL)
	entry := LedgerEntry{
		Method:       event.Request.Method,
		Path:         path,
		ResourceType: event.ResourceType.String(),
		Decision:     "allowed",
	}

	s.mu.Lock()
	s.requestCount++
	if reason == "" && s.requestCount > s.limits.MaxRequests {
		reason = "request_limit"
	}
	if reason != "" {
		entry.Decision = "blocked"
		entry.Reason = reason
	}
	index := len(s.ledger)
	s.ledger = append(s.ledger, entry)
	if event.NetworkID != "" {
		s.requestIndex[event.NetworkID] = index
	}
	s.mu.Unlock()

	if reason != "" {
		_ = chromedp.Run(ctx, fetch.FailRequest(event.RequestID, network.ErrorReasonBlockedByClient))
		if reason == "request_limit" {
			s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic request limit exceeded"))
		} else {
			s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_NAVIGATION, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic request blocked by origin or robots policy"))
		}
		return
	}
	if err := chromedp.Run(ctx, fetch.ContinueRequest(event.RequestID)); err != nil && ctx.Err() == nil {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_SESSION_LOST, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, "browser session lost while authorizing request"))
	}
}

func (s *executionState) authorizeURL(raw string) (string, string) {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" || parsed.User != nil {
		return "/invalid", "invalid_url"
	}
	if parsed.Scheme+"://"+parsed.Host != s.allowedOrigin {
		return "/external", "external_origin"
	}
	path := parsed.EscapedPath()
	if path == "" {
		path = "/"
	}
	if parsed.RawQuery != "" {
		path += "?synthetic"
	}
	if !s.robots.Allows(parsed.EscapedPath()) {
		return path, "robots_disallowed"
	}
	return path, ""
}

func (s *executionState) handleResponse(event *network.EventResponseReceived) {
	if event.Response == nil {
		return
	}
	tdmHeader := headerValue(event.Response.Headers, "tdm-reservation")
	contentLength := headerValue(event.Response.Headers, "content-length")
	s.mu.Lock()
	if index, ok := s.requestIndex[event.RequestID]; ok && index < len(s.ledger) {
		s.ledger[index].Status = event.Response.Status
	}
	if event.Type == network.ResourceTypeDocument {
		s.mainStatus = event.Response.Status
		s.mainTDMHeader = tdmHeader
	}
	declaredBytes, lengthErr := strconv.ParseUint(strings.TrimSpace(contentLength), 10, 64)
	projectedOverLimit := lengthErr == nil && (s.responseBytes > s.limits.MaxResponseBytes || declaredBytes > s.limits.MaxResponseBytes-s.responseBytes)
	if index, ok := s.requestIndex[event.RequestID]; ok && index < len(s.ledger) {
		if contentLength == "" || lengthErr != nil {
			s.ledger[index].Decision = "rejected"
			s.ledger[index].Reason = "invalid_content_length"
		} else {
			previousBytes := s.ledger[index].ResponseBytes
			s.ledger[index].ResponseBytes = declaredBytes
			s.responseBytes = s.responseBytes - previousBytes + declaredBytes
			if projectedOverLimit {
				s.ledger[index].Decision = "rejected"
				s.ledger[index].Reason = "declared_response_byte_limit"
			}
		}
	}
	s.mu.Unlock()
	if strings.TrimSpace(tdmHeader) == "1" {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic response reserves text and data mining"))
		return
	}
	if contentLength == "" || lengthErr != nil {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic response omitted a valid content length"))
		return
	}
	if projectedOverLimit {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic declared response bytes exceed the probe limit"))
	}
}

func (s *executionState) handleLoadingFinished(event *network.EventLoadingFinished) {
	if math.IsNaN(event.EncodedDataLength) || math.IsInf(event.EncodedDataLength, 0) || event.EncodedDataLength < 0 || event.EncodedDataLength > math.MaxUint64 {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "invalid response-byte count"))
		return
	}
	actualBytes := uint64(math.Ceil(event.EncodedDataLength))
	s.mu.Lock()
	index, known := s.requestIndex[event.RequestID]
	_, duplicate := s.completed[event.RequestID]
	if !known || index >= len(s.ledger) || duplicate {
		s.mu.Unlock()
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "response bytes could not be matched to the request ledger"))
		return
	}
	s.completed[event.RequestID] = struct{}{}
	overLimit := s.actualBytes > s.limits.MaxResponseBytes || actualBytes > s.limits.MaxResponseBytes-s.actualBytes
	if !overLimit {
		s.actualBytes += actualBytes
	}
	s.mu.Unlock()
	if overLimit {
		s.setFailure(Failure(runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY, "synthetic response-byte limit exceeded"))
	}
}

func headerValue(headers network.Headers, wanted string) string {
	for name, raw := range headers {
		if !strings.EqualFold(name, wanted) {
			continue
		}
		switch value := raw.(type) {
		case string:
			return value
		case float64:
			return strconv.FormatFloat(value, 'f', -1, 64)
		default:
			return fmt.Sprint(value)
		}
	}
	return ""
}

func (s *executionState) setFailure(result *runtimev1.BrowserResult) {
	s.mu.Lock()
	if s.failure == nil {
		s.failure = result
	}
	cancel := s.cancelExecution
	s.mu.Unlock()
	if cancel != nil {
		cancel()
	}
}

func (s *executionState) getFailure() *runtimev1.BrowserResult {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.failure
}

func (s *executionState) getMainStatus() int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.mainStatus
}

func (s *executionState) getMainTDMHeader() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.mainTDMHeader
}

func (s *executionState) snapshot() ([]LedgerEntry, uint64, uint64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	ledger := append([]LedgerEntry(nil), s.ledger...)
	SortLedger(ledger)
	return ledger, s.requestCount, s.responseBytes
}

func classifyExecutionError(err error, sessionStarted bool) *runtimev1.BrowserResult {
	if errors.Is(err, context.DeadlineExceeded) {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, "synthetic browser execution timed out")
	}
	message := strings.ToLower(err.Error())
	if strings.Contains(message, "target") && (strings.Contains(message, "closed") || strings.Contains(message, "lost")) {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_TARGET_LOST, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, "browser target was lost")
	}
	if sessionStarted {
		return Failure(runtimev1.ErrorCode_ERROR_CODE_SESSION_LOST, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, "browser session was lost")
	}
	return Failure(runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY, "could not connect to the pinned browser")
}
