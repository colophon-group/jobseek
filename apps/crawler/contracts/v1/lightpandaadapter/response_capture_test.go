package lightpandaadapter

import (
	"bytes"
	"context"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
	"testing"
)

func responseCaptureInput() *runtimev1.BrowserExecutionInput {
	in := validInput()
	in.Plan.Evaluations = nil
	in.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE}
	in.Plan.Captures = []*runtimev1.CapturePlan{{CaptureId: "feed", Kind: runtimev1.CaptureKind_CAPTURE_KIND_RESPONSE_BODY, MaxBytes: ResponseBodyPayloadLimit}}
	return in
}

func TestResponseCapturePreservesBytesAndBinding(t *testing.T) {
	body := []byte("<?xml version=\"1.0\"?><rss><item><description><![CDATA[<p>Engineer & systems</p>]]></description></item></rss>")
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		status := uint32(200)
		result := NewRunnerSuccess(bound, &RawSuccess{ResourcePolicy: &runtimev1.ResourcePolicySignals{}, FinalURL: bound.Input().Plan.TargetUrl, Status: &status, HTML: []byte{}, Captures: []RawCapture{{CaptureID: "feed", Body: body}}})
		body[0] = '!'
		return result
	}}
	adapter, _ := NewNavigationRenderOnly(runner)
	result := adapter.Execute(context.Background(), responseCaptureInput())
	if result.GetSuccess() == nil || runner.calls != 1 || len(result.GetSuccess().Captures) != 1 {
		t.Fatal(result, runner.calls)
	}
	got := result.GetSuccess().Captures[0]
	if got.CaptureId != "feed" || got.Body.TotalSizeBytes != uint64(len(body)) || !got.Body.Complete || got.Body.Chunks[0].GetInlineBody()[0] != '<' || result.GetSuccess().Html.TotalSizeBytes != 0 {
		t.Fatal("raw body changed or incomplete")
	}
}

func TestResponseCaptureInvalidPlansRefuseBeforeRunner(t *testing.T) {
	for name, change := range map[string]func(*runtimev1.BrowserExecutionInput){
		"missing_capability": func(in *runtimev1.BrowserExecutionInput) {
			in.Plan.RequiredCapabilities = in.Plan.RequiredCapabilities[:1]
		},
		"missing_capture": func(in *runtimev1.BrowserExecutionInput) { in.Plan.Captures = nil },
		"multiple": func(in *runtimev1.BrowserExecutionInput) {
			in.Plan.Captures = append(in.Plan.Captures, proto.Clone(in.Plan.Captures[0]).(*runtimev1.CapturePlan))
		},
		"zero":      func(in *runtimev1.BrowserExecutionInput) { in.Plan.Captures[0].MaxBytes = 0 },
		"oversized": func(in *runtimev1.BrowserExecutionInput) { in.Plan.Captures[0].MaxBytes = ResponseBodyPayloadLimit + 1 },
		"pattern":   func(in *runtimev1.BrowserExecutionInput) { x := ".*"; in.Plan.Captures[0].UrlPattern = &x },
		"artifact":  func(in *runtimev1.BrowserExecutionInput) { in.Plan.Captures[0].ArtifactOnly = true },
		"unknown": func(in *runtimev1.BrowserExecutionInput) {
			in.Plan.Captures[0].ProtoReflect().SetUnknown([]byte{0x80, 0x01, 0x01})
		},
	} {
		t.Run(name, func(t *testing.T) {
			in := responseCaptureInput()
			change(in)
			runner := &fakeRunner{}
			adapter, _ := NewNavigationRenderOnly(runner)
			result := adapter.Execute(context.Background(), in)
			if runner.calls != 0 || result.GetSuccess() != nil {
				t.Fatal(result, runner.calls)
			}
		})
	}
}

func TestResponseCaptureMissingMismatchedAndOversizedResultsFailClosed(t *testing.T) {
	for _, mode := range []string{"missing", "wrong_id", "too_large", "mixed_html", "empty_valid"} {
		t.Run(mode, func(t *testing.T) {
			in := responseCaptureInput()
			in.Plan.Captures[0].MaxBytes = 10
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				status := uint32(200)
				raw := &RawSuccess{ResourcePolicy: &runtimev1.ResourcePolicySignals{}, FinalURL: in.Plan.TargetUrl, Status: &status, HTML: []byte{}, Captures: []RawCapture{{CaptureID: "feed", Body: []byte{}}}}
				switch mode {
				case "missing":
					raw.Captures = nil
				case "wrong_id":
					raw.Captures[0].CaptureID = "other"
				case "too_large":
					raw.Captures[0].Body = bytes.Repeat([]byte{'x'}, 11)
				case "mixed_html":
					raw.HTML = []byte("<html/>")
				}
				return NewRunnerSuccess(bound, raw)
			}}
			adapter, _ := NewNavigationRenderOnly(runner)
			result := adapter.Execute(context.Background(), in)
			if (result.GetSuccess() != nil) != (mode == "empty_valid") {
				t.Fatal(mode, result)
			}
		})
	}
}
