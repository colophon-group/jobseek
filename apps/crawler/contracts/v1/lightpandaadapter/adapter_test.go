package lightpandaadapter

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

type fakeRunner struct {
	calls int
	run   func(context.Context, BoundInput) RunnerOutcome
}

func (runner *fakeRunner) Run(ctx context.Context, bound BoundInput) RunnerOutcome {
	runner.calls++
	return runner.run(ctx, bound)
}

type privacyCall struct {
	evaluationID string
	raw          []byte
	limit        uint64
}

type fakePrivacy struct {
	calls []privacyCall
	seal  func(context.Context, string, []byte, uint64) (*runtimev1.ExtensionEnvelope, error)
}

func (privacy *fakePrivacy) SealEvaluation(
	ctx context.Context,
	evaluationID string,
	raw []byte,
	limit uint64,
) (*runtimev1.ExtensionEnvelope, error) {
	privacy.calls = append(privacy.calls, privacyCall{
		evaluationID: evaluationID,
		raw:          append([]byte(nil), raw...),
		limit:        limit,
	})
	if privacy.seal != nil {
		return privacy.seal(ctx, evaluationID, raw, limit)
	}
	safe := bytes.ReplaceAll(raw, []byte(`"secret"`), []byte(`"[redacted]"`))
	return sealedEnvelope(safe), nil
}

func sealedEnvelope(payload []byte) *runtimev1.ExtensionEnvelope {
	digest := sha256.Sum256(payload)
	return &runtimev1.ExtensionEnvelope{
		SchemaId:      evaluationSchemaID,
		SchemaVersion: evaluationSchemaVersion,
		Encoding:      runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON,
		Payload:       append([]byte(nil), payload...),
		PayloadSha256: hex.EncodeToString(digest[:]),
	}
}

func validInput() *runtimev1.BrowserExecutionInput {
	return &runtimev1.BrowserExecutionInput{
		Assignment: &runtimev1.BrowserAssignment{
			Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
			CapabilityClass: runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
			ServiceLane:     runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA,
			RoutingRevision: "lightpanda-b1-r1",
		},
		Plan: &runtimev1.BrowserPlan{
			ContractVersion: runtimeContractVersion,
			TargetUrl:       "https://example.test/jobs",
			RequiredCapabilities: []runtimev1.BrowserCapability{
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
			},
			Navigation: &runtimev1.NavigationPlan{
				WaitUntil:       runtimev1.WaitCondition_WAIT_CONDITION_LOAD,
				TimeoutMs:       30_000,
				OriginRequestId: "origin-1",
			},
			Evaluations: []*runtimev1.EvaluationPlan{
				{
					EvaluationId:   "jobs",
					Expression:     "document.title",
					MaxResultBytes: EvaluationPayloadLimit + 1024,
					NetworkEffect:  runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_NONE,
				},
			},
			OriginOperations: []*runtimev1.OriginOperationRef{{
				OriginRequestId:    "origin-1",
				OperationSequence:  1,
				Role:               "navigation",
				RequestFingerprint: "1111111111111111111111111111111111111111111111111111111111111111",
			}},
		},
	}
}

func successfulRaw() *RawSuccess {
	status := uint32(200)
	return &RawSuccess{
		FinalURL: "https://example.test/jobs/final",
		Status:   &status,
		HTML:     []byte("<html>jobs</html>"),
		Evaluations: []RawEvaluation{
			{EvaluationID: "jobs", JSON: []byte(`{"token":"secret"}`)},
		},
	}
}

func successfulOutcome(bound BoundInput) RunnerOutcome {
	return NewRunnerSuccess(bound, successfulRaw())
}

func newSuccessfulAdapter(t *testing.T) (*Adapter, *fakeRunner, *fakePrivacy) {
	t.Helper()
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		return successfulOutcome(bound)
	}}
	privacy := &fakePrivacy{}
	adapter, err := New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	return adapter, runner, privacy
}

func TestExecuteMapsOneShotSuccessAndMandatoryPrivacy(t *testing.T) {
	adapter, runner, privacy := newSuccessfulAdapter(t)
	result := adapter.Execute(context.Background(), validInput())

	if runner.calls != 1 || len(privacy.calls) != 1 {
		t.Fatalf("runner calls = %d, privacy calls = %d", runner.calls, len(privacy.calls))
	}
	if privacy.calls[0].evaluationID != "jobs" || privacy.calls[0].limit != EvaluationPayloadLimit {
		t.Fatalf("privacy calls = %#v", privacy.calls)
	}
	if result.ContractVersion != runtimeContractVersion ||
		result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA || result.GetSuccess() == nil {
		t.Fatalf("unexpected result: %v", result)
	}
	success := result.GetSuccess()
	if success.FinalUrl != "https://example.test/jobs/final" || success.GetStatus() != 200 ||
		len(success.ActionOutcomes) != 0 || len(success.Captures) != 0 || len(success.Artifacts) != 0 ||
		len(success.Evaluations) != 1 {
		t.Fatalf("unexpected success: %v", success)
	}
	if success.Evaluations[0].EvaluationId != "jobs" ||
		string(success.Evaluations[0].Value.Payload) != `{"token":"[redacted]"}` ||
		string(privacy.calls[0].raw) != `{"token":"secret"}` {
		t.Fatalf("evaluation identity/order changed: %v", success.Evaluations)
	}
	assertManifest(t, success.Html, []byte("<html>jobs</html>"))
}

func TestRenderOnlySuccessDoesNotInvokePrivacy(t *testing.T) {
	input := validInput()
	input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
	}
	input.Plan.Evaluations = nil
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		raw := successfulRaw()
		raw.Evaluations = nil
		return NewRunnerSuccess(bound, raw)
	}}
	privacy := &fakePrivacy{}
	adapter, _ := New(runner, privacy)
	result := adapter.Execute(context.Background(), input)
	if result.GetSuccess() == nil || len(result.GetSuccess().Evaluations) != 0 ||
		runner.calls != 1 || len(privacy.calls) != 0 {
		t.Fatalf("render-only result/calls = %v/%d/%d", result, runner.calls, len(privacy.calls))
	}
}

func TestNewRenderOnlyRejectsEvaluationBeforeRunnerContact(t *testing.T) {
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		return successfulOutcome(bound)
	}}
	adapter, err := NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}

	result := adapter.Execute(context.Background(), validInput())
	if runner.calls != 0 {
		t.Fatalf("evaluation input contacted runner %d times", runner.calls)
	}
	if result.GetUnsupported() == nil || !reflect.DeepEqual(
		result.GetUnsupported().Capabilities,
		[]runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE},
	) {
		t.Fatalf("render-only evaluation result = %v", result)
	}
}

func TestNewRenderOnlyExecutesRenderWithoutPrivacy(t *testing.T) {
	input := validInput()
	input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
	}
	input.Plan.Evaluations = nil
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		raw := successfulRaw()
		raw.Evaluations = nil
		return NewRunnerSuccess(bound, raw)
	}}
	adapter, err := NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}

	result := adapter.Execute(context.Background(), input)
	if runner.calls != 1 || result.GetSuccess() == nil || len(result.GetSuccess().Evaluations) != 0 {
		t.Fatalf("render-only result/calls = %v/%d", result, runner.calls)
	}
}

func TestBoundInputIsFullImmutableCloneWithExactFingerprint(t *testing.T) {
	input := validInput()
	original := proto.Clone(input).(*runtimev1.BrowserExecutionInput)
	var firstFingerprint [sha256.Size]byte
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		first := bound.Input()
		encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(first)
		if err != nil {
			t.Fatal(err)
		}
		firstFingerprint = sha256.Sum256(encoded)
		if firstFingerprint != bound.Fingerprint() {
			t.Fatal("fingerprint does not cover complete exact input")
		}
		first.Assignment.RoutingRevision = "mutated"
		first.Plan.Evaluations[0].Expression = "mutated"
		if proto.Equal(first, bound.Input()) {
			t.Fatal("bound accessor exposed mutable state")
		}
		input.Plan.TargetUrl = "https://caller-mutated.invalid/"
		if !proto.Equal(bound.Input(), original) {
			t.Fatal("caller mutation reached bound input")
		}
		return successfulOutcome(bound)
	}}
	privacy := &fakePrivacy{}
	adapter, err := New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	if result := adapter.Execute(context.Background(), input); result.GetSuccess() == nil {
		t.Fatalf("result = %v", result)
	}
	if firstFingerprint == ([sha256.Size]byte{}) {
		t.Fatal("fingerprint was not observed")
	}
}

func TestPreflightNeverInvokesRunnerAndOrdersUnsupported(t *testing.T) {
	tests := []struct {
		name        string
		mutate      func(*runtimev1.BrowserExecutionInput)
		unsupported []runtimev1.BrowserCapability
	}{
		{"nil input", func(input *runtimev1.BrowserExecutionInput) { *input = runtimev1.BrowserExecutionInput{} }, nil},
		{"wrong backend before feature", func(input *runtimev1.BrowserExecutionInput) {
			input.Assignment.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
			input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES)
		}, nil},
		{"wrong class", func(input *runtimev1.BrowserExecutionInput) {
			input.Assignment.CapabilityClass = runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_INTERACTION_CAPTURE
		}, nil},
		{"wrong lane", func(input *runtimev1.BrowserExecutionInput) {
			input.Assignment.ServiceLane = runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_CHROMIUM
		}, nil},
		{"bad revision", func(input *runtimev1.BrowserExecutionInput) { input.Assignment.RoutingRevision = "bad revision" }, nil},
		{"unknown top-level field", func(input *runtimev1.BrowserExecutionInput) {
			input.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
		}, nil},
		{"unknown nested field", func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.Evaluations[0].ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
		}, nil},
		{"duplicate capability", func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER)
		}, nil},
		{"unknown capability", func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.RequiredCapabilities[0] = runtimev1.BrowserCapability(99)
		}, nil},
		{"missing render", func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE}
		}, nil},
		{"unsupported sorted", func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities,
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
			)
		}, []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
		}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			adapter, runner, privacy := newSuccessfulAdapter(t)
			input := validInput()
			test.mutate(input)
			result := adapter.Execute(context.Background(), input)
			if runner.calls != 0 || len(privacy.calls) != 0 {
				t.Fatalf("preflight executed dependencies: runner=%d privacy=%d", runner.calls, len(privacy.calls))
			}
			if test.unsupported != nil {
				if result.GetUnsupported() == nil || !reflect.DeepEqual(result.GetUnsupported().Capabilities, test.unsupported) {
					t.Fatalf("unsupported = %v, want %v", result.GetUnsupported(), test.unsupported)
				}
			} else {
				assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY)
			}
		})
	}
}

func TestCheapCardinalityAndSerializedInputBoundsPrecedeBinding(t *testing.T) {
	tests := map[string]func(*runtimev1.BrowserExecutionInput){
		"unbounded repeated field": func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.Actions = make([]*runtimev1.BrowserAction, 100_000)
		},
		"serialized input ceiling": func(input *runtimev1.BrowserExecutionInput) {
			input.Plan.OriginOperations[0].Role = strings.Repeat("x", InputPayloadLimit)
		},
	}
	for name, mutate := range tests {
		t.Run(name, func(t *testing.T) {
			input := validInput()
			mutate(input)
			adapter, runner, privacy := newSuccessfulAdapter(t)
			assertFailure(t, adapter.Execute(context.Background(), input), runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY)
			if runner.calls != 0 || len(privacy.calls) != 0 {
				t.Fatalf("bounded preflight invoked dependencies: %d/%d", runner.calls, len(privacy.calls))
			}
		})
	}
}

func TestNarrowB1PlanExcludesAllAdjacentFeatures(t *testing.T) {
	frame := "child"
	origin := "eval-origin"
	sessionKey := "session"
	mutations := map[string]func(*runtimev1.BrowserPlan){
		"wrong contract":    func(plan *runtimev1.BrowserPlan) { plan.ContractVersion = "crawler.runtime/v2" },
		"non-http URL":      func(plan *runtimev1.BrowserPlan) { plan.TargetUrl = "file:///tmp/jobs" },
		"navigation absent": func(plan *runtimev1.BrowserPlan) { plan.Navigation = nil },
		"navigation wait": func(plan *runtimev1.BrowserPlan) {
			plan.Navigation.WaitUntil = runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED
		},
		"navigation header": func(plan *runtimev1.BrowserPlan) {
			plan.Navigation.Headers = []*runtimev1.Header{{Name: "x", Value: "y"}}
		},
		"ignore TLS": func(plan *runtimev1.BrowserPlan) { plan.Navigation.IgnoreTlsErrors = true },
		"session":    func(plan *runtimev1.BrowserPlan) { plan.Session = &runtimev1.SessionPlan{SessionKey: &sessionKey} },
		"action":     func(plan *runtimev1.BrowserPlan) { plan.Actions = []*runtimev1.BrowserAction{{ActionId: "a"}} },
		"capture":    func(plan *runtimev1.BrowserPlan) { plan.Captures = []*runtimev1.CapturePlan{{CaptureId: "c"}} },
		"interception": func(plan *runtimev1.BrowserPlan) {
			plan.Interceptions = []*runtimev1.InterceptionRule{{UrlPattern: "*"}}
		},
		"evaluation frame": func(plan *runtimev1.BrowserPlan) { plan.Evaluations[0].FrameName = &frame },
		"evaluation network": func(plan *runtimev1.BrowserPlan) {
			plan.Evaluations[0].NetworkEffect = runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_ORIGIN_CONTACT
		},
		"evaluation origin": func(plan *runtimev1.BrowserPlan) { plan.Evaluations[0].OriginRequestId = &origin },
		"extra evaluation": func(plan *runtimev1.BrowserPlan) {
			plan.Evaluations = append(plan.Evaluations, proto.Clone(plan.Evaluations[0]).(*runtimev1.EvaluationPlan))
		},
		"evaluation without capability": func(plan *runtimev1.BrowserPlan) {
			plan.RequiredCapabilities = []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}
		},
		"capability without evaluation": func(plan *runtimev1.BrowserPlan) { plan.Evaluations = nil },
		"extra origin": func(plan *runtimev1.BrowserPlan) {
			plan.OriginOperations = append(plan.OriginOperations, proto.Clone(plan.OriginOperations[0]).(*runtimev1.OriginOperationRef))
		},
		"origin mismatch": func(plan *runtimev1.BrowserPlan) { plan.OriginOperations[0].OriginRequestId = "other" },
		"origin parent":   func(plan *runtimev1.BrowserPlan) { plan.OriginOperations[0].ParentOriginRequestId = &origin },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			adapter, runner, privacy := newSuccessfulAdapter(t)
			input := validInput()
			mutate(input.Plan)
			assertFailure(t, adapter.Execute(context.Background(), input), runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG, runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY)
			if runner.calls != 0 || len(privacy.calls) != 0 {
				t.Fatalf("excluded feature reached runner/privacy: %d/%d", runner.calls, len(privacy.calls))
			}
		})
	}
}

func TestOpaqueOutcomeRejectsZeroValueAndWrongBinding(t *testing.T) {
	tests := map[string]func(BoundInput) RunnerOutcome{
		"zero value": func(BoundInput) RunnerOutcome { return RunnerOutcome{} },
		"wrong binding": func(BoundInput) RunnerOutcome {
			other := validInput()
			other.Assignment.RoutingRevision = "other-route"
			otherBound, _, ok := bind(other)
			if !ok {
				t.Fatal("other input did not bind")
			}
			return NewRunnerSuccess(otherBound, successfulRaw())
		},
	}
	for name, makeOutcome := range tests {
		t.Run(name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				return makeOutcome(bound)
			}}
			privacy := &fakePrivacy{}
			adapter, _ := New(runner, privacy)
			assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
			if runner.calls != 1 || len(privacy.calls) != 0 {
				t.Fatalf("calls runner/privacy = %d/%d", runner.calls, len(privacy.calls))
			}
		})
	}
}

func TestOutcomeConstructorClonesRetainedSlicesAndPointers(t *testing.T) {
	mutated := make(chan struct{})
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		raw := successfulRaw()
		outcome := NewRunnerSuccess(bound, raw)
		go func() {
			raw.HTML[0] = 'x'
			raw.Evaluations[0].JSON[0] = '['
			*raw.Status = 503
			close(mutated)
		}()
		<-mutated
		return outcome
	}}
	privacy := &fakePrivacy{}
	adapter, _ := New(runner, privacy)
	result := adapter.Execute(context.Background(), validInput())
	if result.GetSuccess() == nil || result.GetSuccess().GetStatus() != 200 ||
		string(result.GetSuccess().Html.Chunks[0].GetInlineBody()) != "<html>jobs</html>" ||
		string(privacy.calls[0].raw) != `{"token":"secret"}` {
		t.Fatalf("retained caller mutation reached result: %v/%v", result, privacy.calls)
	}
}

func TestRunnerPanicDoesNotEscape(t *testing.T) {
	runner := &fakeRunner{run: func(context.Context, BoundInput) RunnerOutcome { panic("runner") }}
	privacy := &fakePrivacy{}
	adapter, _ := New(runner, privacy)
	assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
	if runner.calls != 1 || len(privacy.calls) != 0 {
		t.Fatalf("calls = %d/%d", runner.calls, len(privacy.calls))
	}
}

func TestCancellationHasNoRetryFallbackOrPartialOutput(t *testing.T) {
	t.Run("before run", func(t *testing.T) {
		adapter, runner, privacy := newSuccessfulAdapter(t)
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		assertFailure(t, adapter.Execute(ctx, validInput()), runtimev1.ErrorCode_ERROR_CODE_CANCELLED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY)
		if runner.calls != 0 || len(privacy.calls) != 0 {
			t.Fatal("cancelled preflight invoked dependencies")
		}
	})
	t.Run("during run", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cleanupCompleted := false
		runner := &fakeRunner{run: func(runContext context.Context, bound BoundInput) RunnerOutcome {
			if _, ok := runContext.Deadline(); !ok {
				t.Fatal("runner did not receive bounded execution context")
			}
			cancel()
			if runContext.Err() == nil {
				t.Fatal("caller cancellation did not reach runner context")
			}
			cleanupCompleted = true
			return successfulOutcome(bound)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		result := adapter.Execute(ctx, validInput())
		assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_CANCELLED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY)
		if runner.calls != 1 || !cleanupCompleted || len(privacy.calls) != 0 || result.GetSuccess() != nil {
			t.Fatalf("calls/cleanup/result = %d/%t/%d/%v", runner.calls, cleanupCompleted, len(privacy.calls), result)
		}
	})
}

func TestRunnerUsesMinimumDeadlineAndCleanupFailureDominates(t *testing.T) {
	t.Run("plan timeout", func(t *testing.T) {
		input := validInput()
		input.Plan.Navigation.TimeoutMs = 10
		runner := &fakeRunner{run: func(ctx context.Context, bound BoundInput) RunnerOutcome {
			<-ctx.Done()
			return successfulOutcome(bound)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		started := time.Now()
		result := adapter.Execute(context.Background(), input)
		assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY)
		if runner.calls != 1 || len(privacy.calls) != 0 || time.Since(started) > time.Second {
			t.Fatalf("timeout was not bounded: calls=%d/%d elapsed=%s", runner.calls, len(privacy.calls), time.Since(started))
		}
	})

	t.Run("caller deadline is earlier", func(t *testing.T) {
		input := validInput()
		input.Plan.Navigation.TimeoutMs = 30_000
		parent, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		defer cancel()
		parentDeadline, _ := parent.Deadline()
		runner := &fakeRunner{run: func(ctx context.Context, bound BoundInput) RunnerOutcome {
			deadline, ok := ctx.Deadline()
			if !ok || !deadline.Equal(parentDeadline) {
				t.Fatalf("runner deadline = %v/%t, want parent %v", deadline, ok, parentDeadline)
			}
			<-ctx.Done()
			return successfulOutcome(bound)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		assertFailure(t, adapter.Execute(parent, input), runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY)
	})

	t.Run("cleanup failure dominates timeout", func(t *testing.T) {
		input := validInput()
		input.Plan.Navigation.TimeoutMs = 10
		runner := &fakeRunner{run: func(ctx context.Context, bound BoundInput) RunnerOutcome {
			<-ctx.Done()
			return NewRunnerCleanupFailure(bound)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		assertFailure(t, adapter.Execute(context.Background(), input), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
		if len(privacy.calls) != 0 {
			t.Fatal("cleanup failure reached privacy")
		}
	})
}

func TestOutcomeIdentityAndShapeFailuresDiscardEverything(t *testing.T) {
	tests := map[string]func(BoundInput) RunnerOutcome{
		"wrong fingerprint": func(bound BoundInput) RunnerOutcome {
			outcome := successfulOutcome(bound)
			outcome.bindingFingerprint[0] ^= 1
			return outcome
		},
		"no outcome": func(BoundInput) RunnerOutcome { return RunnerOutcome{} },
		"wrong evaluation identity": func(bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Evaluations[0].EvaluationID = "other"
			return NewRunnerSuccess(bound, raw)
		},
		"extra evaluation": func(bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Evaluations = append(raw.Evaluations, RawEvaluation{EvaluationID: "extra", JSON: []byte("null")})
			return NewRunnerSuccess(bound, raw)
		},
		"bad final URL": func(bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.FinalURL = "javascript:alert(1)"
			return NewRunnerSuccess(bound, raw)
		},
		"invalid UTF-8 final URL": func(bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.FinalURL = "https://example.test/\xff"
			return NewRunnerSuccess(bound, raw)
		},
		"oversized evaluation identity": func(bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Evaluations[0].EvaluationID = strings.Repeat("x", maxEvaluationIDBytes+1)
			return NewRunnerSuccess(bound, raw)
		},
	}
	for name, outcome := range tests {
		t.Run(name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return outcome(bound) }}
			privacy := &fakePrivacy{}
			adapter, _ := New(runner, privacy)
			result := adapter.Execute(context.Background(), validInput())
			assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
			if result.GetSuccess() != nil || len(privacy.calls) != 0 {
				t.Fatalf("partial output/privacy escaped: %v/%d", result, len(privacy.calls))
			}
		})
	}
}

func TestTypedRunnerFailureIsClosedAndDoesNotInvokePrivacy(t *testing.T) {
	for _, test := range []struct {
		name       string
		failure    ProviderFailure
		wantCode   runtimev1.ErrorCode
		wantPolicy runtimev1.ErrorDisposition
	}{
		{"valid", ProviderFailure{Code: runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY}, runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY},
		{"invalid pair", ProviderFailure{Code: runtimev1.ErrorCode_ERROR_CODE_TIMEOUT, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY}, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY},
		{"excluded code", ProviderFailure{Code: runtimev1.ErrorCode_ERROR_CODE_ANTI_BOT, Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY}, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY},
	} {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				return NewRunnerFailure(bound, test.failure)
			}}
			privacy := &fakePrivacy{}
			adapter, _ := New(runner, privacy)
			assertFailure(t, adapter.Execute(context.Background(), validInput()), test.wantCode, test.wantPolicy)
			if runner.calls != 1 || len(privacy.calls) != 0 {
				t.Fatalf("calls = %d/%d", runner.calls, len(privacy.calls))
			}
		})
	}
}

func TestEvaluationLimitsAndSealedEnvelopeValidationFailClosed(t *testing.T) {
	t.Run("non-sensitive accepted envelope is detached", func(t *testing.T) {
		var provided *runtimev1.ExtensionEnvelope
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Evaluations[0].JSON = []byte(`{"count":12}`)
			return NewRunnerSuccess(bound, raw)
		}}
		privacy := &fakePrivacy{seal: func(_ context.Context, _ string, raw []byte, _ uint64) (*runtimev1.ExtensionEnvelope, error) {
			provided = sealedEnvelope(raw)
			return provided, nil
		}}
		adapter, _ := New(runner, privacy)
		result := adapter.Execute(context.Background(), validInput())
		if result.GetSuccess() == nil {
			t.Fatalf("result = %v", result)
		}
		provided.Payload[0] = 'x'
		provided.PayloadSha256 = strings.Repeat("0", 64)
		value := result.GetSuccess().Evaluations[0].Value
		if string(value.Payload) != `{"count":12}` || value.PayloadSha256 == provided.PayloadSha256 {
			t.Fatal("result aliases privacy envelope")
		}
	})

	t.Run("privacy panic", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return successfulOutcome(bound) }}
		privacy := &fakePrivacy{seal: func(context.Context, string, []byte, uint64) (*runtimev1.ExtensionEnvelope, error) {
			panic("privacy")
		}}
		adapter, _ := New(runner, privacy)
		result := adapter.Execute(context.Background(), validInput())
		assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
		if len(privacy.calls) != 1 || result.GetSuccess() != nil {
			t.Fatalf("privacy calls/result = %d/%v", len(privacy.calls), result)
		}
	})

	t.Run("cancellation during privacy", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return successfulOutcome(bound) }}
		privacy := &fakePrivacy{seal: func(_ context.Context, _ string, raw []byte, _ uint64) (*runtimev1.ExtensionEnvelope, error) {
			cancel()
			return sealedEnvelope(raw), nil
		}}
		adapter, _ := New(runner, privacy)
		result := adapter.Execute(ctx, validInput())
		assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_CANCELLED, runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY)
		if len(privacy.calls) != 1 || result.GetSuccess() != nil {
			t.Fatalf("privacy calls/result = %d/%v", len(privacy.calls), result)
		}
	})

	t.Run("raw uses caller lower bound", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Evaluations[0].JSON = bytes.Repeat([]byte("1"), 33)
			return NewRunnerSuccess(bound, raw)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		input := validInput()
		input.Plan.Evaluations[0].MaxResultBytes = 32
		assertFailure(t, adapter.Execute(context.Background(), input), runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY)
		if len(privacy.calls) != 0 {
			t.Fatal("prevalidation permitted partial privacy calls")
		}
	})

	badEnvelopes := map[string]func(*runtimev1.ExtensionEnvelope){
		"schema":  func(envelope *runtimev1.ExtensionEnvelope) { envelope.SchemaId = "other" },
		"version": func(envelope *runtimev1.ExtensionEnvelope) { envelope.SchemaVersion = 2 },
		"encoding": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.Encoding = runtimev1.ExtensionEncoding_EXTENSION_ENCODING_PROTOBUF
		},
		"digest": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.PayloadSha256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
		},
		"uppercase digest": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.PayloadSha256 = strings.ToUpper(envelope.PayloadSha256)
		},
		"output over registration ceiling": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.Payload = append([]byte{'"'}, bytes.Repeat([]byte("x"), int(EvaluationPayloadLimit)-1)...)
			envelope.Payload = append(envelope.Payload, '"')
			digest := sha256.Sum256(envelope.Payload)
			envelope.PayloadSha256 = hex.EncodeToString(digest[:])
		},
		"unknown envelope field": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
		},
		"invalid json": func(envelope *runtimev1.ExtensionEnvelope) {
			envelope.Payload = []byte("{")
			digest := sha256.Sum256(envelope.Payload)
			envelope.PayloadSha256 = hex.EncodeToString(digest[:])
		},
	}
	for name, mutate := range badEnvelopes {
		t.Run(name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return successfulOutcome(bound) }}
			privacy := &fakePrivacy{seal: func(_ context.Context, _ string, raw []byte, _ uint64) (*runtimev1.ExtensionEnvelope, error) {
				envelope := sealedEnvelope(raw)
				mutate(envelope)
				return envelope, nil
			}}
			adapter, _ := New(runner, privacy)
			result := adapter.Execute(context.Background(), validInput())
			assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
			if result.GetSuccess() != nil {
				t.Fatal("sealed envelope failure leaked output")
			}
		})
	}

	t.Run("privacy failure discards all provider output", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return successfulOutcome(bound) }}
		privacy := &fakePrivacy{seal: func(_ context.Context, _ string, _ []byte, _ uint64) (*runtimev1.ExtensionEnvelope, error) {
			return nil, errors.New("reject")
		}}
		adapter, _ := New(runner, privacy)
		result := adapter.Execute(context.Background(), validInput())
		assertFailure(t, result, runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
		if len(privacy.calls) != 1 || result.GetSuccess() != nil {
			t.Fatalf("privacy calls/result = %d/%v", len(privacy.calls), result)
		}
	})
}

func TestHTMLMappingIsBoundedChunkedAndDetached(t *testing.T) {
	html := bytes.Repeat([]byte("x"), HTMLChunkLimit+3)
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
		raw := successfulRaw()
		raw.HTML = html
		return NewRunnerSuccess(bound, raw)
	}}
	privacy := &fakePrivacy{}
	adapter, _ := New(runner, privacy)
	result := adapter.Execute(context.Background(), validInput())
	if result.GetSuccess() == nil {
		t.Fatalf("result = %v", result)
	}
	manifest := result.GetSuccess().Html
	assertManifest(t, manifest, html)
	if len(manifest.Chunks) != 2 || len(manifest.Chunks[0].GetInlineBody()) != HTMLChunkLimit || len(manifest.Chunks[1].GetInlineBody()) != 3 {
		t.Fatalf("chunk sizes = %d/%d", len(manifest.Chunks[0].GetInlineBody()), len(manifest.Chunks[1].GetInlineBody()))
	}
	html[0] = 'z'
	if manifest.Chunks[0].GetInlineBody()[0] != 'x' {
		t.Fatal("result aliases runner HTML")
	}

	t.Run("over ceiling", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.HTML = make([]byte, HTMLPayloadLimit+1)
			return NewRunnerSuccess(bound, raw)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY)
		if len(privacy.calls) != 0 {
			t.Fatal("oversized HTML reached privacy")
		}
	})
}

func TestSuccessRequiresStatusAndHTML(t *testing.T) {
	t.Run("missing status rejected", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.Status = nil
			return NewRunnerSuccess(bound, raw)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
		if len(privacy.calls) != 0 {
			t.Fatal("missing status reached privacy")
		}
	})

	t.Run("missing HTML rejected", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.HTML = nil
			return NewRunnerSuccess(bound, raw)
		}}
		privacy := &fakePrivacy{}
		adapter, _ := New(runner, privacy)
		assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
		if len(privacy.calls) != 0 {
			t.Fatal("missing HTML reached privacy")
		}
	})

	t.Run("present empty HTML accepted", func(t *testing.T) {
		runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
			raw := successfulRaw()
			raw.HTML = []byte{}
			return NewRunnerSuccess(bound, raw)
		}}
		adapter, _ := New(runner, &fakePrivacy{})
		result := adapter.Execute(context.Background(), validInput())
		if result.GetSuccess() == nil || result.GetSuccess().Html == nil {
			t.Fatalf("present empty HTML rejected: %v", result)
		}
		assertManifest(t, result.GetSuccess().Html, []byte{})
	})

	for _, test := range []struct {
		name   string
		status uint32
	}{
		{name: "status 99 rejected", status: 99},
		{name: "status 600 rejected", status: 600},
	} {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				raw := successfulRaw()
				raw.Status = &test.status
				return NewRunnerSuccess(bound, raw)
			}}
			privacy := &fakePrivacy{}
			adapter, _ := New(runner, privacy)
			assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
			if len(privacy.calls) != 0 {
				t.Fatal("invalid status reached privacy")
			}
		})
	}

	for _, test := range []struct {
		name   string
		status uint32
	}{
		{name: "status 100 accepted", status: 100},
		{name: "status 599 accepted", status: 599},
	} {
		t.Run(test.name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				raw := successfulRaw()
				raw.Status = &test.status
				return NewRunnerSuccess(bound, raw)
			}}
			adapter, _ := New(runner, &fakePrivacy{})
			result := adapter.Execute(context.Background(), validInput())
			if result.GetSuccess() == nil || result.GetSuccess().GetStatus() != test.status {
				t.Fatalf("boundary status %d rejected: %v", test.status, result)
			}
		})
	}
}

func TestDependenciesAreMandatory(t *testing.T) {
	runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome { return successfulOutcome(bound) }}
	privacy := &fakePrivacy{}
	if adapter, err := New(nil, privacy); err == nil || adapter != nil {
		t.Fatal("nil runner accepted")
	}
	if adapter, err := New(runner, nil); err == nil || adapter != nil {
		t.Fatal("nil privacy accepted")
	}
	if adapter, err := NewRenderOnly(nil); err == nil || adapter != nil {
		t.Fatal("render-only adapter accepted nil runner")
	}
	var adapter *Adapter
	assertFailure(t, adapter.Execute(context.Background(), validInput()), runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
}

func assertFailure(t *testing.T, result *runtimev1.BrowserResult, code runtimev1.ErrorCode, disposition runtimev1.ErrorDisposition) {
	t.Helper()
	if result == nil || result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA ||
		result.ContractVersion != runtimeContractVersion || result.GetError() == nil ||
		result.GetError().Error == nil || result.GetError().Error.Code != code ||
		result.GetError().Error.Disposition != disposition || result.GetSuccess() != nil ||
		len(result.GetError().DiagnosticArtifacts) != 0 {
		t.Fatalf("failure = %v, want %s/%s", result, code, disposition)
	}
}

func assertManifest(t *testing.T, manifest *runtimev1.ChunkManifest, content []byte) {
	t.Helper()
	if manifest == nil || !manifest.Complete || manifest.TotalSizeBytes != uint64(len(content)) {
		t.Fatalf("manifest header = %v", manifest)
	}
	totalDigest := sha256.Sum256(content)
	if manifest.TotalSha256 != hex.EncodeToString(totalDigest[:]) {
		t.Fatalf("total digest = %q", manifest.TotalSha256)
	}
	var joined []byte
	for index, chunk := range manifest.Chunks {
		body := chunk.GetInlineBody()
		digest := sha256.Sum256(body)
		if chunk.Sequence != uint32(index) || chunk.SizeBytes != uint64(len(body)) ||
			chunk.Sha256 != hex.EncodeToString(digest[:]) || chunk.GetArtifact() != nil {
			t.Fatalf("chunk %d invalid: %v", index, chunk)
		}
		joined = append(joined, body...)
	}
	if !bytes.Equal(joined, content) {
		t.Fatalf("joined content differs: %d/%d", len(joined), len(content))
	}
}
