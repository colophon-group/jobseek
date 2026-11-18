package main

import (
	"bytes"
	"context"
	"errors"
	"github.com/chromedp/cdproto/network"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"testing"
	"time"
)

func TestResponseBodyWaitRequiresFinalDocumentCompletion(t *testing.T) {
	state := newNavigationState("main")
	state.observe(&network.EventRequestWillBeSent{RequestID: "document", FrameID: "main", Type: network.ResourceTypeDocument}, time.Now())
	state.observe(&network.EventLoadingFinished{RequestID: "other"}, time.Now())
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Millisecond)
	defer cancel()
	if err := waitForDocumentBody(ctx, state, "document"); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal(err)
	}
	state.observe(&network.EventLoadingFinished{RequestID: "document"}, time.Now())
	if err := waitForDocumentBody(context.Background(), state, "document"); err != nil {
		t.Fatal(err)
	}
	state.observe(&network.EventRequestWillBeSent{RequestID: "replacement", FrameID: "main", Type: network.ResourceTypeDocument}, time.Now())
	if err := waitForDocumentBody(context.Background(), state, "document"); err == nil {
		t.Fatal("changed document accepted")
	}
}

func TestResponseCaptureMaximumFitsExistingFrameAndSealsBytes(t *testing.T) {
	body := bytes.Repeat([]byte{'x'}, int(lightpandaadapter.ResponseBodyPayloadLimit))
	in := bridgeInput("https://example.test/feed", "", 0)
	in.Plan.RequiredCapabilities = append(in.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE)
	in.Plan.Captures = []*runtimev1.CapturePlan{{CaptureId: "feed", Kind: runtimev1.CaptureKind_CAPTURE_KIND_RESPONSE_BODY, MaxBytes: lightpandaadapter.ResponseBodyPayloadLimit}}
	adapter, _ := lightpandaadapter.NewNavigationRenderOnly(runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
		if task.ResponseBodyLimit != lightpandaadapter.ResponseBodyPayloadLimit || task.Evaluation != nil {
			t.Fatal(task)
		}
		return Result{Status: 200, FinalURL: in.Plan.TargetUrl, HTMLPresent: true, ResourcePolicy: &runtimev1.ResourcePolicySignals{}, ResponseBody: body}, nil
	}})
	result := adapter.Execute(context.Background(), in)
	if result.GetSuccess() == nil {
		t.Fatal(result)
	}
	safe := sanitizeRuntimeV1Result(result)
	if safe.GetSuccess() == nil || safe.GetSuccess().Captures[0].Body.TotalSizeBytes != uint64(len(body)) {
		t.Fatal(safe)
	}
	var record bytes.Buffer
	err := writeRuntimeV1Result(&record, safe)
	if err != nil || uint64(record.Len()) > runtimeV1ResultFrameLimit {
		t.Fatal(err, record.Len())
	}
	safe.GetSuccess().Captures[0].Body.Chunks[0].GetInlineBody()[0] = '!'
	if sanitizeRuntimeV1Result(safe).GetSuccess() != nil {
		t.Fatal("corrupt raw bytes accepted")
	}
}
