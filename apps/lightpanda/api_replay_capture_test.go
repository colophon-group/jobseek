package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/chromedp/cdproto/network"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func replayTestCapture(t *testing.T) *replayCapture {
	t.Helper()
	c, e := newReplayCapture(api.BrowserReplayOptions{Inventory: api.Options{Endpoint: "https://example.com/api?stored=1", Method: "POST", Path: "jobs"}})
	if e != nil {
		t.Fatal(e)
	}
	return c
}
func replayTestRequest(c *replayCapture, id, source string) {
	c.observe(&network.EventRequestWillBeSent{RequestID: network.RequestID(id), Type: network.ResourceTypeFetch, Request: &network.Request{URL: source, Method: "POST", Headers: network.Headers{"authorization": "private-token"}}})
}
func replayTestResponse(c *replayCapture, id string, headers network.Headers) {
	c.observe(&network.EventResponseReceived{RequestID: network.RequestID(id), Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://example.com/api?fresh=2", Status: 200, Headers: headers}})
	c.observe(&network.EventLoadingFinished{RequestID: network.RequestID(id), EncodedDataLength: 100})
}
func TestAPIReplayCaptureCorrelatesCompletedPrivateExchanges(t *testing.T) {
	c := replayTestCapture(t)
	defer c.erase()
	replayTestRequest(c, "foreign", "https://other.com/api")
	replayTestRequest(c, "first", "https://example.com/api?fresh=1")
	replayTestRequest(c, "second", "https://example.com/api?fresh=2")
	replayTestResponse(c, "foreign", network.Headers{})
	replayTestResponse(c, "first", network.Headers{})
	replayTestResponse(c, "second", network.Headers{})
	calls := 0
	exchanges, e := c.exchanges(context.Background(), func(_ context.Context, id network.RequestID) ([]byte, error) {
		calls++
		if id == "first" {
			return []byte(`{"jobs":[{},{}]}`), nil
		}
		return []byte(`{"jobs":[{}]}`), nil
	})
	if e != nil || len(exchanges) != 2 || calls != 2 {
		t.Fatal("capture correlation differs", e, calls)
	}
	h, d, matched, e := api.SelectBrowserReplayExchange(api.BrowserReplayOptions{Inventory: api.Options{Endpoint: "https://example.com/api", Method: "POST", Path: "jobs"}}, exchanges)
	if e != nil || !matched || d == nil || h.Get("Authorization") != "private-token" {
		t.Fatal("selected capture lost local auth", e)
	}
	if _, e = json.Marshal(c); e == nil || strings.Contains(fmt.Sprintf("%+v %#v", c, c), "private-token") {
		t.Fatal("capture exposed credentials")
	}
}
func TestAPIReplayCaptureDenialIsTerminalBeforeBodyRead(t *testing.T) {
	for _, headers := range []network.Headers{{"tdm-reservation": "1"}, {"tdm-reservation": 42}} {
		c := replayTestCapture(t)
		replayTestRequest(c, "one", "https://example.com/api")
		replayTestResponse(c, "one", headers)
		calls := 0
		_, e := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) { calls++; return nil, nil })
		if e == nil || calls != 0 {
			t.Fatal("publisher rejection read body", e, calls)
		}
		c.erase()
	}
	c := replayTestCapture(t)
	replayTestRequest(c, "one", "https://example.com/api")
	replayTestResponse(c, "one", network.Headers{})
	_, e := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		return []byte(`<meta name="tdm-reservation" content="1">`), nil
	})
	var denied *policy.Reservation
	if !errors.As(e, &denied) {
		t.Fatal("body policy denial became invalid JSON skip", e)
	}
	c.erase()
}
func TestAPIReplayCaptureRedirectFailureBoundsAndCleanup(t *testing.T) {
	c := replayTestCapture(t)
	replayTestRequest(c, "one", "https://example.com/api")
	c.observe(&network.EventRequestWillBeSent{RequestID: "one", RedirectResponse: &network.Response{Status: 302}})
	replayTestResponse(c, "one", network.Headers{})
	exchanges, e := c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		t.Fatal("redirect reused credentials")
		return nil, nil
	})
	if e != nil || len(exchanges) != 0 {
		t.Fatal("redirect capture retained", e)
	}
	for i := 0; i <= replayCaptureLimit; i++ {
		replayTestRequest(c, fmt.Sprint(i), "https://example.com/api")
	}
	_, e = c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) { return nil, nil })
	if !errors.Is(e, errResourceLimit) {
		t.Fatal("unbounded capture", e)
	}
	c.erase()
	if len(c.pending) != 0 || len(c.completed) != 0 {
		t.Fatal("capture cleanup retained private state")
	}
	c = replayTestCapture(t)
	replayTestRequest(c, "bad", "https://example.com/api")
	replayTestResponse(c, "bad", network.Headers{})
	exchanges, e = c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) { return []byte("invalid JSON"), nil })
	if e != nil || len(exchanges) != 0 {
		t.Fatal("invalid JSON became authority", e)
	}
	replayTestRequest(c, "big", "https://example.com/api")
	replayTestResponse(c, "big", network.Headers{})
	_, e = c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		return make([]byte, replayCaptureBodyLimit+1), nil
	})
	if !errors.Is(e, errResourceLimit) {
		t.Fatal("decoded body limit missing", e)
	}
	c.erase()
	_, e = c.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) { return nil, nil })
	if !errors.Is(e, errReplayCapture) {
		t.Fatal("closed capture remained usable", e)
	}
}
