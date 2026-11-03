package main

import (
	"errors"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestNativeBrowserBodyLimitAcceptsObservedPageSizeWithoutWideningGenericReplay(t *testing.T) {
	o, _, e := api.NativeBrowserOptions("bytedance", "https://joinbytedance.com/search", `{}`)
	if e != nil {
		t.Fatal(e)
	}
	p, _ := api.ByteDanceOptionsFromURL("https://joinbytedance.com/search")
	r := p.PageRequest(0, nil)
	body := `{"code":0,"padding":"` + strings.Repeat("x", 4194874) + `"}`
	response := replayFetchResponse{Status: 200, URL: r.URL, Body: body}
	if _, e = replayResponseDocument(o, r, response); e != nil {
		t.Fatal("actual-size native page rejected", e)
	}
	generic := replayFetchOptions(t)
	response.URL = generic.Inventory.Endpoint
	if _, e = replayResponseDocument(generic, api.Request{Method: generic.Inventory.Method, URL: response.URL}, response); !errors.Is(e, errResourceLimit) {
		t.Fatal("generic replay limit widened", e)
	}
	response.URL = r.URL
	response.Body = strings.Repeat("x", (16<<20)+1)
	if _, e = replayResponseDocument(o, r, response); !errors.Is(e, errResourceLimit) {
		t.Fatal("native page became unbounded", e)
	}
	if _, _, e = api.NativeBrowserOptions("bytedance", "https://joinbytedance.com/search", `{"response_body_limit":33554432}`); e == nil {
		t.Fatal("metadata controlled resource limit")
	}
	expression, e := replayFetchExpression(o, r)
	if e != nil || !strings.Contains(expression, "body.length>16777216") {
		t.Fatal("browser bridge limit differs")
	}
	capture, e := newReplayCapture(o)
	if e != nil || capture.bodyLimit != 16<<20 {
		t.Fatal("capture limit differs")
	}
}
