package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"sync"

	"github.com/chromedp/cdproto/network"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

var errReplayCapture = errors.New("API browser capture failed")
var errReplayCredentialResponse = errors.Join(errReplayCapture, errors.New("API response reflected private credentials"))

const replayCaptureLimit = 32
const replayCaptureBodyLimit = 2_000_000

type replayPending struct {
	source, method string
	headers        http.Header
	signals        *runtimev1.ResourcePolicySignals
	response       bool
}

// A capture belongs to one fresh browser target. CDP listeners only correlate
// events; response bodies are read outside the listener so CDP cannot deadlock.
// Request credentials never become task, result or diagnostic fields.
type replayCapture struct {
	mu        sync.Mutex
	endpoint  *url.URL
	method    string
	pending   map[network.RequestID]replayPending
	completed chan network.RequestID
	failure   error
	closed    bool
}

func (*replayCapture) String() string               { return "private API browser capture" }
func (*replayCapture) GoString() string             { return "private API browser capture" }
func (*replayCapture) MarshalJSON() ([]byte, error) { return nil, errReplayCapture }

func newReplayCapture(o api.BrowserReplayOptions) (*replayCapture, error) {
	endpoint, e := url.Parse(o.Inventory.Endpoint)
	if e != nil || endpoint.Host == "" || endpoint.User != nil || endpoint.Scheme != "https" && endpoint.Scheme != "http" || o.Inventory.Method != "GET" && o.Inventory.Method != "POST" {
		return nil, errReplayCapture
	}
	return &replayCapture{endpoint: endpoint, method: o.Inventory.Method, pending: map[network.RequestID]replayPending{}, completed: make(chan network.RequestID, replayCaptureLimit)}, nil
}
func (c *replayCapture) matches(source, method string) bool {
	u, e := url.Parse(source)
	return e == nil && u.User == nil && u.Scheme == c.endpoint.Scheme && u.Host == c.endpoint.Host && u.EscapedPath() == c.endpoint.EscapedPath() && method == c.method
}
func (c *replayCapture) observe(event any) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || c.failure != nil {
		return
	}
	switch e := event.(type) {
	case *network.EventRequestWillBeSent:
		// A redirect cannot confer an unrelated request's credential authority.
		if e.RedirectResponse != nil {
			delete(c.pending, e.RequestID)
			return
		}
		if e.Request == nil || e.RequestID == "" || e.Type != network.ResourceTypeXHR && e.Type != network.ResourceTypeFetch || !c.matches(e.Request.URL, e.Request.Method) {
			return
		}
		if len(c.pending) >= replayCaptureLimit {
			c.failure = errResourceLimit
			return
		}
		headers := http.Header{}
		total := 0
		for name, raw := range e.Request.Headers {
			value, ok := raw.(string)
			total += len(name) + len(value)
			if !ok || len(name) > 256 || len(value) > 8192 || strings.ContainsAny(name+value, "\x00\r\n") || total > 64<<10 {
				c.failure = errReplayCapture
				return
			}
			headers.Set(name, value)
		}
		c.pending[e.RequestID] = replayPending{source: e.Request.URL, method: e.Request.Method, headers: headers}
	case *network.EventResponseReceived:
		pending, ok := c.pending[e.RequestID]
		if !ok {
			return
		}
		if e.Response == nil || !c.matches(e.Response.URL, pending.method) || e.Type != network.ResourceTypeXHR && e.Type != network.ResourceTypeFetch {
			delete(c.pending, e.RequestID)
			return
		}
		signals, invalid := mainDocumentPolicySignals(e.Response.Headers)
		if invalid {
			c.failure = policy.ErrSignals
			return
		}
		if replayReflectsCredentials(pending.headers, "", signals.TdmReservationHeader, signals.TdmPolicyHeader) {
			c.failure = errReplayCredentialResponse
			return
		}
		// Publisher denial is terminal and never falls through to another replay.
		if err := policy.Check(signals, "", c.endpoint.String()); err != nil {
			c.failure = err
			return
		}
		pending.signals = signals
		pending.response = true
		c.pending[e.RequestID] = pending
	case *network.EventLoadingFailed:
		delete(c.pending, e.RequestID)
	case *network.EventLoadingFinished:
		pending, ok := c.pending[e.RequestID]
		if !ok || !pending.response {
			return
		}
		if e.EncodedDataLength > replayCaptureBodyLimit {
			c.failure = errResourceLimit
			return
		}
		select {
		case c.completed <- e.RequestID:
		default:
			c.failure = errResourceLimit
		}
	}
}

// Called while the same target is alive, after navigation and settle. Invalid
// JSON exchanges are skipped just as in Python; policy errors never are.
func (c *replayCapture) exchanges(ctx context.Context, read func(context.Context, network.RequestID) ([]byte, error)) ([]api.BrowserReplayExchange, error) {
	if read == nil {
		return nil, errReplayCapture
	}
	out := []api.BrowserReplayExchange{}
	bytes := 0
	for {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		c.mu.Lock()
		failure := c.failure
		closed := c.closed
		c.mu.Unlock()
		if failure != nil {
			return nil, failure
		}
		if closed {
			return nil, errReplayCapture
		}
		select {
		case id := <-c.completed:
			c.mu.Lock()
			pending, ok := c.pending[id]
			delete(c.pending, id)
			c.mu.Unlock()
			if !ok {
				continue
			}
			body, e := read(ctx, id)
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			if e != nil || len(body) < 2 {
				continue
			}
			bytes += len(body)
			if len(body) > replayCaptureBodyLimit || bytes > 16*replayCaptureBodyLimit {
				return nil, errResourceLimit
			}
			var reservation, policyURL *string
			if pending.signals != nil {
				reservation = pending.signals.TdmReservationHeader
				policyURL = pending.signals.TdmPolicyHeader
			}
			if replayReflectsCredentials(pending.headers, string(body), reservation, policyURL) {
				return nil, errReplayCredentialResponse
			}
			if e = policy.Check(pending.signals, string(body), c.endpoint.String()); e != nil {
				return nil, e
			}
			exchange, e := api.NewBrowserReplayExchange(pending.source, pending.method, pending.headers, body)
			if e == nil {
				out = append(out, exchange)
			}
		default:
			return out, nil
		}
	}
}
func (c *replayCapture) erase() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closed = true
	clear(c.pending)
	for {
		select {
		case <-c.completed:
		default:
			return
		}
	}
}
