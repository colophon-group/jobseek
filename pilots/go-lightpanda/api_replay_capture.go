package main

import (
	"context"
	"errors"
	"net/http"
	"net/url"
	"slices"
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
	sequence       int
	status         int
}

// A capture belongs to one fresh browser target. CDP listeners only correlate
// events; response bodies are read outside the listener so CDP cannot deadlock.
// Request credentials never become task, result or diagnostic fields.
type replayCapture struct {
	mu               sync.Mutex
	endpoint         *url.URL
	options          api.BrowserReplayOptions
	method           string
	bodyLimit        int
	pending          map[network.RequestID]replayPending
	completed        chan network.RequestID
	failure          error
	closed           bool
	responseSequence int
	fixedMatches     func(string, string) bool
}

func (*replayCapture) String() string               { return "private API browser capture" }
func (*replayCapture) GoString() string             { return "private API browser capture" }
func (*replayCapture) MarshalJSON() ([]byte, error) { return nil, errReplayCapture }

func newReplayCapture(o api.BrowserReplayOptions) (*replayCapture, error) {
	endpoint, e := url.Parse(o.Inventory.Endpoint)
	if e != nil || endpoint.Host == "" || endpoint.User != nil || endpoint.Scheme != "https" && endpoint.Scheme != "http" || o.Inventory.Method != "GET" && o.Inventory.Method != "POST" {
		return nil, errReplayCapture
	}
	return &replayCapture{endpoint: endpoint, options: o, method: o.Inventory.Method, bodyLimit: replayBodyLimit(o), pending: map[network.RequestID]replayPending{}, completed: make(chan network.RequestID, replayCaptureLimit)}, nil
}
func (c *replayCapture) matches(source, method string) bool {
	if c.fixedMatches != nil {
		return c.fixedMatches(source, method)
	}
	u, e := url.Parse(source)
	return e == nil && u.User == nil && u.Scheme == c.endpoint.Scheme && u.Host == c.endpoint.Host && (u.EscapedPath() == c.endpoint.EscapedPath() && method == c.method || c.options.LiveURLMatches(source, method))
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
		resource := c.options.PublicCaptureResource(pending.source)
		if c.fixedMatches != nil {
			resource = pending.source
		}
		if err := policy.Check(signals, "", resource); err != nil {
			c.failure = err
			return
		}
		pending.signals = signals
		pending.status = int(e.Response.Status)
		pending.response = true
		c.responseSequence++
		pending.sequence = c.responseSequence
		c.pending[e.RequestID] = pending
	case *network.EventLoadingFailed:
		delete(c.pending, e.RequestID)
	case *network.EventLoadingFinished:
		pending, ok := c.pending[e.RequestID]
		if !ok || !pending.response {
			return
		}
		if e.EncodedDataLength > float64(c.bodyLimit) {
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
	sequences := []int{}
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
			if e != nil && c.options.LiveURLMatch == "" || len(body) < 2 && c.options.LiveURLMatch == "" {
				continue
			}
			bytes += len(body)
			if len(body) > c.bodyLimit || bytes > 16*c.bodyLimit {
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
			if e = policy.Check(pending.signals, string(body), c.options.PublicCaptureResource(pending.source)); e != nil {
				return nil, e
			}
			constructor := api.NewBrowserReplayExchange
			if c.options.LiveURLMatch != "" {
				constructor = api.NewLiveBrowserReplayExchange
			}
			exchange, e := constructor(pending.source, pending.method, pending.headers, body)
			if e == nil {
				at := len(out)
				if c.options.LiveURLMatch != "" {
					for at > 0 && sequences[at-1] > pending.sequence {
						at--
					}
				}
				out = slices.Insert(out, at, exchange)
				sequences = slices.Insert(sequences, at, pending.sequence)
			}
		default:
			return out, nil
		}
	}
}

// A new live-discovery handler in Python starts after the failed injected
// fetch. Clear that completed exchange before the same-target retry navigation;
// it must not win the first-response choice over the page's own live request.
func (c *replayCapture) beginLivePass() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return errReplayCapture
	}
	if c.failure != nil {
		return c.failure
	}
	clear(c.pending)
	c.responseSequence = 0
	for {
		select {
		case <-c.completed:
		default:
			return nil
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
