//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/chromedp/cdproto/network"
	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestAPIDetailReturnsSelectedFieldsOnlyAfterCleanup(t *testing.T) {
	request := replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Operation: "detail", BoardURL: "https://example.com/job/1", Metadata: json.RawMessage(`{"fields":{"title":"title","description":"body"},"settle":0}`), TimeoutMS: 20000}
	for _, mode := range []string{"success", "empty", "cleanup", "publisher", "publisher-cleanup"} {
		t.Run(mode, func(t *testing.T) {
			cleaned := false
			execution := &runtimeV1ServiceExecution{dayforceRun: func(ctx context.Context, _ Config, task Task) (Result, error) {
				if task.APIReplay.detailConverse == nil || task.URL != request.BoardURL || task.Navigation.timeout.Milliseconds() != 20000 {
					t.Fatal("detail task contract differs")
				}
				if strings.HasPrefix(mode, "publisher") {
					err := error(&policy.Reservation{URL: "https://example.com/api", Source: "header"})
					if mode == "publisher-cleanup" {
						err = errors.Join(err, errCleanupUnproved)
					}
					return Result{}, err
				}
				var documents []*api.Document
				if mode != "empty" {
					d, err := api.Decode([]byte(`{"data":{"title":"Engineer","body":"<p>Complete original role</p>","unused":"unselected"}}`))
					if err != nil {
						t.Fatal(err)
					}
					documents = []*api.Document{d}
				}
				if err := task.APIReplay.detailConverse(ctx, documents); err != nil {
					return Result{}, err
				}
				if mode == "cleanup" {
					return Result{}, errCleanupUnproved
				}
				cleaned = true
				return Result{apiReplaySessionSettled: true}, nil
			}}
			response, err := execution.executeAPIReplay(context.Background(), request)
			if err != nil || !response.Valid() {
				t.Fatal("invalid response", err)
			}
			if mode == "success" || mode == "empty" {
				if !cleaned || response.Outcome != "success" || len(response.Inventory) != 0 {
					t.Fatal("detail escaped before cleanup")
				}
				var fields map[string]any
				if json.Unmarshal(response.Detail, &fields) != nil {
					t.Fatal("invalid detail")
				}
				if mode == "success" && (fields["title"] != "Engineer" || fields["description"] != "<p>Complete original role</p>" || len(fields) != 2) {
					t.Fatal("selected fields differ")
				}
				if mode == "empty" && len(fields) != 0 {
					t.Fatal("missing original job fabricated")
				}
			} else if len(response.Detail) != 0 || mode == "publisher" && response.Outcome != "publisher_reserved" || mode != "publisher" && response.Outcome != "failed" {
				t.Fatal("provisional detail or policy escaped failed cleanup")
			}
		})
	}
	request.Metadata = json.RawMessage(`{"proxy":true}`)
	execution := &runtimeV1ServiceExecution{dayforceRun: func(context.Context, Config, Task) (Result, error) {
		t.Fatal("unsupported launch configuration executed")
		return Result{}, nil
	}}
	response, err := execution.executeAPIReplay(context.Background(), request)
	if err != nil || response.Outcome != "invalid_config" {
		t.Fatal("unsupported config not refused")
	}
}

func TestDetailCaptureRetainsOriginalTelemetryExclusion(t *testing.T) {
	for _, source := range []string{"https://example.com/api/job", "https://api.example.net/detail?job=1"} {
		if !detailCaptureMatches(source, "POST") {
			t.Fatal("public detail capture excluded")
		}
	}
	for _, source := range []string{"https://example.com/analytics", "https://user:password@example.com/api", "file:///tmp/job"} {
		if detailCaptureMatches(source, "GET") {
			t.Fatal("invalid capture admitted")
		}
	}
}

func TestDetailCaptureSelectionUsesResponseOrderAndRetainsPolicyRefusal(t *testing.T) {
	c := replayTestCapture(t)
	c.fixedMatches = detailCaptureMatches
	c.preserveResponseOrder = true
	defer c.erase()
	replayTestRequest(c, "first", "https://example.com/api/job")
	replayTestRequest(c, "second", "https://api.example.net/job")
	c.observe(&network.EventResponseReceived{RequestID: "first", Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://example.com/api/job", Status: 200, Headers: network.Headers{}}})
	c.observe(&network.EventResponseReceived{RequestID: "second", Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://api.example.net/job", Status: 200, Headers: network.Headers{}}})
	c.observe(&network.EventLoadingFinished{RequestID: "second", EncodedDataLength: 100})
	c.observe(&network.EventLoadingFinished{RequestID: "first", EncodedDataLength: 100})
	exchanges, err := c.exchanges(context.Background(), func(_ context.Context, id network.RequestID) ([]byte, error) {
		return []byte(`{"title":"` + string(id) + `","description":"A complete original description of more than fifty characters."}`), nil
	})
	if err != nil || len(exchanges) != 2 {
		t.Fatal("bounded detail capture failed", err)
	}
	documents := []*api.Document{api.BrowserReplayDocument(exchanges[0]), api.BrowserReplayDocument(exchanges[1])}
	_, object, err := api.FindSingleJob(context.Background(), documents, "")
	if err != nil || object["title"] != "first" {
		t.Fatal("response ordering changed equal-score choice")
	}
	denied := replayTestCapture(t)
	denied.fixedMatches = detailCaptureMatches
	defer denied.erase()
	replayTestRequest(denied, "one", "https://api.example.net/job")
	denied.observe(&network.EventResponseReceived{RequestID: "one", Type: network.ResourceTypeFetch, Response: &network.Response{URL: "https://api.example.net/job", Status: 200, Headers: network.Headers{"tdm-reservation": "1"}}})
	_, err = denied.exchanges(context.Background(), func(context.Context, network.RequestID) ([]byte, error) {
		t.Fatal("reserved detail body read")
		return nil, nil
	})
	var reservation *policy.Reservation
	if !errors.As(err, &reservation) {
		t.Fatal("detail reservation lost")
	}
}
