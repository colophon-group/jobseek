package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strings"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

type bridgePrivacyCall struct {
	evaluationID string
	raw          []byte
	limit        uint64
}

// bridgeFixturePrivacy is test-only. It deliberately proves adapter wiring,
// not the production privacy boundary that remains required before activation.
type bridgeFixturePrivacy struct {
	calls []bridgePrivacyCall
}

func (privacy *bridgeFixturePrivacy) SealEvaluation(
	ctx context.Context,
	evaluationID string,
	raw []byte,
	limit uint64,
) (*runtimev1.ExtensionEnvelope, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	privacy.calls = append(privacy.calls, bridgePrivacyCall{
		evaluationID: evaluationID,
		raw:          append([]byte(nil), raw...),
		limit:        limit,
	})
	digest := sha256.Sum256(raw)
	return &runtimev1.ExtensionEnvelope{
		SchemaId:      "jobseek.browser.evaluation-json",
		SchemaVersion: 1,
		Encoding:      runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON,
		Payload:       append([]byte(nil), raw...),
		PayloadSha256: hex.EncodeToString(digest[:]),
	}, nil
}

func TestRuntimeV1RunnerTranslatesB0AndB1ExactlyOnce(t *testing.T) {
	for _, test := range []struct {
		name           string
		expression     string
		wantEvaluation bool
	}{
		{name: "B0 render only"},
		{name: "B1 evaluation", expression: "document.title", wantEvaluation: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			input := bridgeInput("https://example.test/jobs", test.expression, 17)
			calls := 0
			runner := runtimeV1Runner{
				config: Config{Binary: "/fixed/lightpanda"},
				run: func(_ context.Context, config Config, task Task) (Result, error) {
					calls++
					if config.Binary != "/fixed/lightpanda" || config.TaskTimeout != 1250*time.Millisecond {
						t.Fatalf("config = %#v", config)
					}
					if task.URL != input.Plan.TargetUrl {
						t.Fatalf("task = %#v", task)
					}
					if test.wantEvaluation {
						if task.Evaluation == nil || task.Evaluation.Expression != test.expression ||
							task.Evaluation.MaxResultBytes != 17 {
							t.Fatalf("evaluation = %#v", task.Evaluation)
						}
						return Result{
							Status: 201, FinalURL: input.Plan.TargetUrl, HTML: "<html>bridge</html>", HTMLPresent: true,
							Expression: []byte(`"fixture"`),
						}, nil
					}
					if task.Evaluation != nil {
						t.Fatalf("B0 evaluation = %#v", task.Evaluation)
					}
					return Result{
						Status: 201, FinalURL: input.Plan.TargetUrl, HTML: "<html>bridge</html>", HTMLPresent: true,
					}, nil
				},
			}
			privacy := &bridgeFixturePrivacy{}
			adapter, err := lightpandaadapter.New(runner, privacy)
			if err != nil {
				t.Fatal(err)
			}
			result := adapter.Execute(context.Background(), input)
			if calls != 1 || result.GetSuccess() == nil {
				t.Fatalf("calls/result = %d/%v", calls, result)
			}
			success := result.GetSuccess()
			if success.GetStatus() != 201 || success.FinalUrl != input.Plan.TargetUrl ||
				!bytes.Equal(bridgeManifestBody(success.Html), []byte("<html>bridge</html>")) {
				t.Fatalf("success = %v", success)
			}
			if test.wantEvaluation {
				if len(success.Evaluations) != 1 || len(privacy.calls) != 1 ||
					privacy.calls[0].evaluationID != "evaluation-1" ||
					privacy.calls[0].limit != 17 || string(privacy.calls[0].raw) != `"fixture"` {
					t.Fatalf("evaluations/privacy = %v/%#v", success.Evaluations, privacy.calls)
				}
			} else if len(success.Evaluations) != 0 || len(privacy.calls) != 0 {
				t.Fatalf("B0 evaluations/privacy = %v/%#v", success.Evaluations, privacy.calls)
			}
		})
	}
}

func TestRuntimeV1RunnerPreservesHTMLPresence(t *testing.T) {
	t.Run("missing HTML fails closed", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "document.title", 64)
		runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
			return Result{
				Status: 200, FinalURL: input.Plan.TargetUrl, HTML: "",
				Expression: []byte(`"value"`),
			}, nil
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		assertBridgeFailure(
			t,
			adapter.Execute(context.Background(), input),
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
		if len(privacy.calls) != 0 {
			t.Fatal("missing HTML reached privacy")
		}
	})

	t.Run("present empty HTML is accepted", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "", 0)
		runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
			return Result{
				Status: 200, FinalURL: input.Plan.TargetUrl, HTML: "", HTMLPresent: true,
			}, nil
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		result := adapter.Execute(context.Background(), input)
		if result.GetSuccess() == nil || result.GetSuccess().Html == nil ||
			result.GetSuccess().Html.TotalSizeBytes != 0 || !result.GetSuccess().Html.Complete ||
			len(bridgeManifestBody(result.GetSuccess().Html)) != 0 {
			t.Fatalf("present-empty HTML result = %v", result)
		}
		if len(privacy.calls) != 0 {
			t.Fatal("B0 present-empty HTML reached privacy")
		}
	})
}

func TestRuntimeV1RunnerRejectsForgedStatusBeforeConversion(t *testing.T) {
	for _, test := range []struct {
		name   string
		status int
	}{
		{name: "negative", status: -1},
		{name: "wide value that truncates to HTTP status", status: int(uint64(1)<<32) + 200},
	} {
		t.Run(test.name, func(t *testing.T) {
			input := bridgeInput("https://example.test/jobs", "document.title", 64)
			runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
				return Result{
					Status: test.status, FinalURL: input.Plan.TargetUrl,
					HTML: "<html></html>", HTMLPresent: true, Expression: []byte(`"value"`),
				}, nil
			}}
			privacy := &bridgeFixturePrivacy{}
			adapter, err := lightpandaadapter.New(runner, privacy)
			if err != nil {
				t.Fatal(err)
			}
			assertBridgeFailure(
				t,
				adapter.Execute(context.Background(), input),
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
			if len(privacy.calls) != 0 {
				t.Fatal("invalid status reached privacy")
			}
		})
	}
}

func TestRuntimeV1RunnerHonorsCallerEvaluationLimit(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 4)
	runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
		if task.Evaluation == nil || task.Evaluation.MaxResultBytes != 4 {
			t.Fatalf("evaluation = %#v", task.Evaluation)
		}
		result := Result{
			Status: 200, FinalURL: input.Plan.TargetUrl, HTML: "<html></html>", HTMLPresent: true,
			Expression: []byte(`"five"`),
		}
		return Result{}, validateResult(task, result)
	}}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	assertBridgeFailure(
		t,
		adapter.Execute(context.Background(), input),
		runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
	)
	if len(privacy.calls) != 0 {
		t.Fatal("oversized raw evaluation reached privacy")
	}
}

func TestRuntimeV1RunnerPreservesPilotOutputBoundaries(t *testing.T) {
	t.Run("HTML exact limit", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "", 0)
		html := strings.Repeat("h", maxHTMLBytes)
		runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
			result := Result{Status: 200, FinalURL: input.Plan.TargetUrl, HTML: html, HTMLPresent: true}
			if err := validateResult(task, result); err != nil {
				return Result{}, err
			}
			return result, nil
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		result := adapter.Execute(context.Background(), input)
		if result.GetSuccess() == nil || len(bridgeManifestBody(result.GetSuccess().Html)) != maxHTMLBytes ||
			len(privacy.calls) != 0 {
			t.Fatalf("exact HTML result/privacy = %v/%d", result, len(privacy.calls))
		}
	})

	t.Run("HTML above limit", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "", 0)
		runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
			result := Result{
				Status: 200, FinalURL: input.Plan.TargetUrl,
				HTML: strings.Repeat("h", maxHTMLBytes+1), HTMLPresent: true,
			}
			return Result{}, validateResult(task, result)
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		assertBridgeFailure(
			t,
			adapter.Execute(context.Background(), input),
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
		if len(privacy.calls) != 0 {
			t.Fatal("oversized HTML reached privacy")
		}
	})

	t.Run("evaluation exact global limit", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "document.title", uint64(maxExpressionResult))
		evaluation := bridgeJSONString(maxExpressionResult)
		runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
			if task.Evaluation == nil || task.Evaluation.MaxResultBytes != maxExpressionResult {
				t.Fatalf("evaluation = %#v", task.Evaluation)
			}
			result := Result{
				Status: 200, FinalURL: input.Plan.TargetUrl, HTML: "<html></html>", HTMLPresent: true,
				Expression: evaluation,
			}
			if err := validateResult(task, result); err != nil {
				return Result{}, err
			}
			return result, nil
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		result := adapter.Execute(context.Background(), input)
		if result.GetSuccess() == nil || len(privacy.calls) != 1 ||
			len(privacy.calls[0].raw) != maxExpressionResult || privacy.calls[0].limit != uint64(maxExpressionResult) {
			t.Fatalf("exact evaluation result/privacy = %v/%#v", result, privacy.calls)
		}
	})

	t.Run("plan above global and result above global", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "document.title", uint64(maxExpressionResult+1))
		runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
			if task.Evaluation == nil || task.Evaluation.MaxResultBytes != maxExpressionResult {
				t.Fatalf("evaluation was not capped: %#v", task.Evaluation)
			}
			result := Result{
				Status: 200, FinalURL: input.Plan.TargetUrl, HTML: "<html></html>", HTMLPresent: true,
				Expression: bridgeJSONString(maxExpressionResult + 1),
			}
			return Result{}, validateResult(task, result)
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		assertBridgeFailure(
			t,
			adapter.Execute(context.Background(), input),
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
		if len(privacy.calls) != 0 {
			t.Fatal("above-global evaluation reached privacy")
		}
	})
}

func TestRuntimeV1RunnerCleanupFailureDominatesDeadline(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 64)
	input.Plan.Navigation.TimeoutMs = 5
	runner := runtimeV1Runner{run: func(ctx context.Context, _ Config, _ Task) (Result, error) {
		<-ctx.Done()
		return Result{}, errors.Join(ctx.Err(), errCleanupUnproved)
	}}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	assertBridgeFailure(
		t,
		adapter.Execute(context.Background(), input),
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
	if len(privacy.calls) != 0 {
		t.Fatal("cleanup failure reached privacy")
	}
}

func TestRuntimeV1RunnerAuthenticatesContextFailures(t *testing.T) {
	t.Run("genuine deadline", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "document.title", 64)
		input.Plan.Navigation.TimeoutMs = 5
		runner := runtimeV1Runner{run: func(ctx context.Context, _ Config, _ Task) (Result, error) {
			<-ctx.Done()
			return Result{}, ctx.Err()
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		assertBridgeFailure(
			t,
			adapter.Execute(context.Background(), input),
			runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		)
		if len(privacy.calls) != 0 {
			t.Fatal("deadline failure reached privacy")
		}
	})

	t.Run("genuine cancellation", func(t *testing.T) {
		input := bridgeInput("https://example.test/jobs", "document.title", 64)
		callerCtx, cancelCaller := context.WithCancel(context.Background())
		runner := runtimeV1Runner{run: func(ctx context.Context, _ Config, _ Task) (Result, error) {
			cancelCaller()
			<-ctx.Done()
			return Result{}, ctx.Err()
		}}
		privacy := &bridgeFixturePrivacy{}
		adapter, err := lightpandaadapter.New(runner, privacy)
		if err != nil {
			t.Fatal(err)
		}
		assertBridgeFailure(
			t,
			adapter.Execute(callerCtx, input),
			runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
		)
		if len(privacy.calls) != 0 {
			t.Fatal("cancellation failure reached privacy")
		}
	})

	for _, test := range []struct {
		name string
		err  error
	}{
		{name: "deadline masquerade", err: errors.Join(errors.New("provider"), context.DeadlineExceeded)},
		{name: "cancellation masquerade", err: errors.Join(errors.New("provider"), context.Canceled)},
	} {
		t.Run(test.name, func(t *testing.T) {
			input := bridgeInput("https://example.test/jobs", "document.title", 64)
			runner := runtimeV1Runner{run: func(ctx context.Context, _ Config, _ Task) (Result, error) {
				if ctx.Err() != nil {
					t.Fatalf("runner context unexpectedly ended: %v", ctx.Err())
				}
				return Result{}, test.err
			}}
			privacy := &bridgeFixturePrivacy{}
			adapter, err := lightpandaadapter.New(runner, privacy)
			if err != nil {
				t.Fatal(err)
			}
			assertBridgeFailure(
				t,
				adapter.Execute(context.Background(), input),
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
			if len(privacy.calls) != 0 {
				t.Fatal("masquerade failure reached privacy")
			}
		})
	}
}

func TestRuntimeV1RunnerContainsExecutorPanicBeforeAdapterBoundary(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 64)
	events := &eventLog{}
	process := newFakeProcess(events, true)
	_, deps := testRunner(process, taskExecutorFunc(func(context.Context, string, Task) (Result, error) {
		events.add("execute-panic")
		panic("provider detail must not escape")
	}), func(int) bool {
		events.add("verify-listener")
		return false
	})
	runner := runtimeV1Runner{
		config: Config{CleanupTimeout: 500 * time.Millisecond, TerminateGrace: 100 * time.Millisecond},
		run: func(ctx context.Context, config Config, task Task) (Result, error) {
			return runTaskWithDependencies(ctx, config, deps, task)
		},
	}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	result := adapter.Execute(context.Background(), input)
	assertBridgeFailure(
		t,
		result,
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
	assertOrdered(t, events.snapshot(), "execute-panic", "signal-15", "wait-reaped", "verify-listener")
	if strings.Contains(result.GetError().Error.Message, "provider detail") || len(privacy.calls) != 0 {
		t.Fatalf("panic detail/privacy escaped: %v/%#v", result, privacy.calls)
	}
}

func TestRuntimeV1RunnerCleanupFailureDominatesTimedOutExecutorPanic(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 64)
	input.Plan.Navigation.TimeoutMs = 5
	events := &eventLog{}
	process := newFakeProcess(events, true)
	_, deps := testRunner(process, taskExecutorFunc(func(ctx context.Context, _ string, _ Task) (Result, error) {
		<-ctx.Done()
		events.add("execute-panic")
		panic("late provider panic")
	}), func(int) bool {
		events.add("verify-listener")
		return true
	})
	runner := runtimeV1Runner{
		config: Config{CleanupTimeout: 20 * time.Millisecond, TerminateGrace: 5 * time.Millisecond},
		run: func(ctx context.Context, config Config, task Task) (Result, error) {
			return runTaskWithDependencies(ctx, config, deps, task)
		},
	}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	assertBridgeFailure(
		t,
		adapter.Execute(context.Background(), input),
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
	assertOrdered(t, events.snapshot(), "execute-panic", "signal-15", "wait-reaped", "verify-listener")
	if len(privacy.calls) != 0 {
		t.Fatal("failed cleanup reached privacy")
	}
}

func TestRuntimeV1AdapterPreflightNeverStartsOneShot(t *testing.T) {
	for _, test := range []struct {
		name   string
		mutate func(*runtimev1.BrowserExecutionInput, *context.Context)
		code   runtimev1.ErrorCode
	}{
		{
			name: "wrong assignment",
			mutate: func(input *runtimev1.BrowserExecutionInput, _ *context.Context) {
				input.Assignment.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
			},
			code: runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
		},
		{
			name: "unsupported capability",
			mutate: func(input *runtimev1.BrowserExecutionInput, _ *context.Context) {
				input.Plan.RequiredCapabilities = append(
					input.Plan.RequiredCapabilities,
					runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
				)
			},
			code: runtimev1.ErrorCode_ERROR_CODE_UNSPECIFIED,
		},
		{
			name: "pre-cancelled",
			mutate: func(_ *runtimev1.BrowserExecutionInput, ctx *context.Context) {
				cancelled, cancel := context.WithCancel(*ctx)
				cancel()
				*ctx = cancelled
			},
			code: runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			input := bridgeInput("https://example.test/jobs", "document.title", 64)
			ctx := context.Background()
			test.mutate(input, &ctx)
			starts := 0
			runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
				starts++
				return validResult(), nil
			}}
			privacy := &bridgeFixturePrivacy{}
			adapter, err := lightpandaadapter.New(runner, privacy)
			if err != nil {
				t.Fatal(err)
			}
			result := adapter.Execute(ctx, input)
			if starts != 0 || len(privacy.calls) != 0 {
				t.Fatalf("preflight started dependencies: %d/%d", starts, len(privacy.calls))
			}
			if test.name == "unsupported capability" {
				if result.GetUnsupported() == nil || len(result.GetUnsupported().Capabilities) != 1 ||
					result.GetUnsupported().Capabilities[0] != runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS {
					t.Fatalf("unsupported result = %v", result)
				}
				return
			}
			if result.GetError() == nil || result.GetError().Error == nil || result.GetError().Error.Code != test.code {
				t.Fatalf("preflight result = %v, want %s", result, test.code)
			}
		})
	}
}

func TestRuntimeV1RunnerClonesCallerInputAndResult(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 64)
	originalURL := input.Plan.TargetUrl
	rawEvaluation := []byte(`"stable"`)
	runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
		if task.URL != originalURL || task.Evaluation == nil || task.Evaluation.Expression != "document.title" {
			t.Fatalf("task changed before execution: %#v", task)
		}
		return Result{
			Status: 200, FinalURL: originalURL, HTML: "<html>stable</html>", HTMLPresent: true,
			Expression: rawEvaluation,
		}, nil
	}}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	result := adapter.Execute(context.Background(), input)
	if result.GetSuccess() == nil {
		t.Fatalf("result = %v", result)
	}
	input.Plan.TargetUrl = "https://caller-mutated.invalid/"
	input.Plan.Evaluations[0].Expression = "caller-mutated"
	rawEvaluation[1] = 'X'
	if result.GetSuccess().FinalUrl != originalURL ||
		string(result.GetSuccess().Evaluations[0].Value.Payload) != `"stable"` ||
		string(privacy.calls[0].raw) != `"stable"` {
		t.Fatalf("caller mutation reached output/privacy: %v/%#v", result, privacy.calls)
	}
}

func TestRuntimeV1RunnerMapsOpaqueProviderFailure(t *testing.T) {
	input := bridgeInput("https://example.test/jobs", "document.title", 64)
	runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
		return Result{}, errors.New("provider included secret detail")
	}}
	privacy := &bridgeFixturePrivacy{}
	adapter, err := lightpandaadapter.New(runner, privacy)
	if err != nil {
		t.Fatal(err)
	}
	result := adapter.Execute(context.Background(), input)
	assertBridgeFailure(
		t,
		result,
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
	if strings.Contains(result.GetError().Error.Message, "secret detail") || len(privacy.calls) != 0 {
		t.Fatalf("provider detail/privacy escaped: %v/%#v", result, privacy.calls)
	}
}

func bridgeInput(targetURL string, expression string, resultLimit uint64) *runtimev1.BrowserExecutionInput {
	capabilities := []runtimev1.BrowserCapability{
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
	}
	var evaluations []*runtimev1.EvaluationPlan
	if expression != "" {
		capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE)
		evaluations = []*runtimev1.EvaluationPlan{{
			EvaluationId:   "evaluation-1",
			Expression:     expression,
			MaxResultBytes: resultLimit,
			NetworkEffect:  runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_NONE,
		}}
	}
	return &runtimev1.BrowserExecutionInput{
		Assignment: &runtimev1.BrowserAssignment{
			Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
			CapabilityClass: runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
			ServiceLane:     runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA,
			RoutingRevision: "lightpanda-bridge-r1",
		},
		Plan: &runtimev1.BrowserPlan{
			ContractVersion:      "crawler.runtime/v1",
			TargetUrl:            targetURL,
			RequiredCapabilities: capabilities,
			Navigation: &runtimev1.NavigationPlan{
				WaitUntil:       runtimev1.WaitCondition_WAIT_CONDITION_LOAD,
				TimeoutMs:       1250,
				OriginRequestId: "origin-1",
			},
			Evaluations: evaluations,
			OriginOperations: []*runtimev1.OriginOperationRef{{
				OriginRequestId:    "origin-1",
				OperationSequence:  1,
				Role:               "navigation",
				RequestFingerprint: strings.Repeat("1", 64),
			}},
		},
	}
}

func bridgeManifestBody(manifest *runtimev1.ChunkManifest) []byte {
	if manifest == nil {
		return nil
	}
	var body []byte
	for _, chunk := range manifest.Chunks {
		body = append(body, chunk.GetInlineBody()...)
	}
	return body
}

func bridgeJSONString(size int) []byte {
	if size < 2 {
		panic("JSON string size must include two quotes")
	}
	return []byte(`"` + strings.Repeat("x", size-2) + `"`)
}

func assertBridgeFailure(
	t *testing.T,
	result *runtimev1.BrowserResult,
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) {
	t.Helper()
	if result == nil || result.GetError() == nil || result.GetError().Error == nil ||
		result.GetError().Error.Code != code || result.GetError().Error.Disposition != disposition ||
		result.GetSuccess() != nil {
		t.Fatalf("result = %v, want %s/%s", result, code, disposition)
	}
}
