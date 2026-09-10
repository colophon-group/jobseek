package main

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
	"io"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	framing "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

type runtimeV1ExecutorFunc func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult

func (function runtimeV1ExecutorFunc) Execute(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) *runtimev1.BrowserResult {
	return function(ctx, input)
}

type runtimeV1AdapterRunnerFunc func(context.Context, lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome

func (function runtimeV1AdapterRunnerFunc) Run(
	ctx context.Context,
	bound lightpandaadapter.BoundInput,
) lightpandaadapter.RunnerOutcome {
	return function(ctx, bound)
}

func TestRuntimeV1StdioRenderOnlyUsesAdapter(t *testing.T) {
	request := bridgeInput("https://example.test/private-target", "", 0)
	runnerCalls := 0
	runner := runtimeV1Runner{run: func(_ context.Context, _ Config, task Task) (Result, error) {
		runnerCalls++
		if task.URL != request.Plan.TargetUrl || task.Evaluation != nil {
			t.Fatalf("render task = %#v", task)
		}
		return Result{
			Status: 201, FinalURL: request.Plan.TargetUrl,
			HTML: "<html>rendered</html>", HTMLPresent: true,
		}, nil
	}}
	adapter, err := lightpandaadapter.NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}

	var output bytes.Buffer
	if code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &output, adapter); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	result := decodeRuntimeV1Result(t, output.Bytes())
	if runnerCalls != 1 || result.GetSuccess() == nil || result.GetSuccess().GetStatus() != 201 ||
		string(bridgeManifestBody(result.GetSuccess().Html)) != "<html>rendered</html>" {
		t.Fatalf("calls/result = %d/%v", runnerCalls, result)
	}
}

func TestRuntimeV1StdioRejectsEvaluationBeforeRunnerContact(t *testing.T) {
	request := bridgeInput("https://example.test/private-target", "document.title", 64)
	runnerCalls := 0
	runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
		runnerCalls++
		return Result{}, errors.New("must not run")
	}}
	adapter, err := lightpandaadapter.NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}

	var output bytes.Buffer
	if code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &output, adapter); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	result := decodeRuntimeV1Result(t, output.Bytes())
	if runnerCalls != 0 || result.GetUnsupported() == nil || len(result.GetUnsupported().Capabilities) != 1 ||
		result.GetUnsupported().Capabilities[0] != runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE {
		t.Fatalf("calls/result = %d/%v", runnerCalls, result)
	}
}

func TestRuntimeV1StdioInputFailuresAreTypedAndDoNotExecute(t *testing.T) {
	valid := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	var oversizePrefix [binary.MaxVarintLen64]byte
	prefixBytes := binary.PutUvarint(oversizePrefix[:], runtimeV1InputFrameLimit)
	tests := []struct {
		name        string
		data        []byte
		code        runtimev1.ErrorCode
		disposition runtimev1.ErrorDisposition
	}{
		{
			name: "malformed protobuf", data: frameRuntimeV1Payload(t, []byte{0xff}),
			code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		},
		{
			name: "oversize", data: oversizePrefix[:prefixBytes],
			code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		},
		{
			name: "trailing byte", data: append(bytes.Clone(valid), 0),
			code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		},
		{
			name: "second record", data: append(bytes.Clone(valid), valid...),
			code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		},
		{
			name: "noncanonical prefix", data: []byte{0x80, 0x00},
			code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			executions := 0
			var output bytes.Buffer
			code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(bytes.NewReader(test.data)), &output,
				runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
					executions++
					return nil
				}),
			)
			if code != 0 || executions != 0 {
				t.Fatalf("exit/executions = %d/%d", code, executions)
			}
			assertRuntimeV1StdioFailure(t, decodeRuntimeV1Result(t, output.Bytes()), test.code, test.disposition)
		})
	}
}

func TestRuntimeV1StdioInvalidConfigDoesNotContactRunner(t *testing.T) {
	runnerCalls := 0
	runner := runtimeV1Runner{run: func(context.Context, Config, Task) (Result, error) {
		runnerCalls++
		return Result{}, errors.New("must not run")
	}}
	adapter, err := lightpandaadapter.NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	code := runRuntimeV1Stdio(
		context.Background(),
		runtimeV1BorrowedInput(frameRuntimeV1Input(t, &runtimev1.BrowserExecutionInput{})),
		&output,
		adapter,
	)
	if code != 0 || runnerCalls != 0 {
		t.Fatalf("exit/runner calls = %d/%d", code, runnerCalls)
	}
	assertRuntimeV1StdioFailure(
		t,
		decodeRuntimeV1Result(t, output.Bytes()),
		runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
	)
}

func TestRuntimeV1StdioReadFailureIsBoundedAndRedacted(t *testing.T) {
	var output bytes.Buffer
	code := runRuntimeV1Stdio(
		context.Background(),
		runtimeV1BorrowedInput(errorReader{err: errors.New("https://secret.example/expression-token")}),
		&output,
		runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
			t.Fatal("read failure reached executor")
			return nil
		}),
	)
	if code != 0 || strings.Contains(output.String(), "secret") || uint64(output.Len()) > runtimeV1ResultFrameLimit {
		t.Fatalf("exit/size/output = %d/%d/%q", code, output.Len(), output.Bytes())
	}
	assertRuntimeV1StdioFailure(
		t,
		decodeRuntimeV1Result(t, output.Bytes()),
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
}

func TestRuntimeV1StdioModeRejectsArgumentsWithoutLeakage(t *testing.T) {
	const target = "https://secret.example/token"
	const expression = "secretExpression()"
	var output bytes.Buffer
	code := runRuntimeV1StdioMode(
		context.Background(),
		[]string{runtimeV1StdioFlag, target, expression},
		runtimeV1BorrowedInput(strings.NewReader(target+expression)),
		&output,
		nil,
	)
	if code != 0 || strings.Contains(output.String(), target) || strings.Contains(output.String(), expression) {
		t.Fatalf("exit/output = %d/%q", code, output.Bytes())
	}
	assertRuntimeV1StdioFailure(
		t,
		decodeRuntimeV1Result(t, output.Bytes()),
		runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
	)
}

func TestRuntimeV1StdioSanitizesExecutorFailure(t *testing.T) {
	request := bridgeInput("https://example.test/jobs", "", 0)
	malicious := runtimeV1Failure(
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
	malicious.GetError().Error.Message = "provider response: https://secret.example/token"
	malicious.GetError().Error.DiagnosticDetails = []*runtimev1.DiagnosticDetail{{Key: "secret", Value: "expression"}}
	var output bytes.Buffer
	code := runRuntimeV1Stdio(
		context.Background(),
		runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)),
		&output,
		runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
			return malicious
		}),
	)
	if code != 0 || strings.Contains(output.String(), "secret") || strings.Contains(output.String(), "provider") {
		t.Fatalf("exit/output = %d/%q", code, output.Bytes())
	}
	result := decodeRuntimeV1Result(t, output.Bytes())
	if result.GetError().Error.Message != "execution failed" || len(result.GetError().Error.DiagnosticDetails) != 0 {
		t.Fatalf("unsanitized result = %v", result)
	}
}

func TestRuntimeV1StdioRejectsMalformedExecutorResults(t *testing.T) {
	tests := []struct {
		name   string
		result func() *runtimev1.BrowserResult
		mutate func(*runtimev1.BrowserResult)
	}{
		{
			name: "wrong contract", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) { result.ContractVersion = "crawler.runtime/v2" },
		},
		{
			name: "wrong backend", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) {
				result.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
			},
		},
		{
			name: "typed nil success outcome", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) {
				result.Outcome = (*runtimev1.BrowserResult_Success)(nil)
			},
		},
		{
			name: "typed nil unsupported outcome", result: validRuntimeV1UnsupportedResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.Outcome = (*runtimev1.BrowserResult_Unsupported)(nil)
			},
		},
		{
			name: "typed nil error outcome", result: validRuntimeV1ErrorResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.Outcome = (*runtimev1.BrowserResult_Error)(nil)
			},
		},
		{
			name: "unknown field", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) {
				result.ProtoReflect().SetUnknown([]byte{0xa0, 0x06, 0x01})
			},
		},
		{
			name: "render evaluations", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetSuccess().Evaluations = []*runtimev1.EvaluationValue{{EvaluationId: "secret"}}
			},
		},
		{
			name: "render digest", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) { result.GetSuccess().Html.TotalSha256 = strings.Repeat("0", 64) },
		},
		{
			name: "render oversize", result: func() *runtimev1.BrowserResult { return validRuntimeV1RenderResult(t) },
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetSuccess().Html.TotalSizeBytes = lightpandaadapter.HTMLPayloadLimit + 1
			},
		},
		{
			name: "empty unsupported", result: validRuntimeV1UnsupportedResult,
			mutate: func(result *runtimev1.BrowserResult) { result.GetUnsupported().Capabilities = nil },
		},
		{
			name: "duplicate unsupported", result: validRuntimeV1UnsupportedResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetUnsupported().Capabilities = []runtimev1.BrowserCapability{
					runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
					runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
				}
			},
		},
		{
			name: "unsupported diagnostics", result: validRuntimeV1UnsupportedResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetUnsupported().DiagnosticArtifacts = []*runtimev1.ArtifactHandle{{}}
			},
		},
		{
			name: "failure pair", result: validRuntimeV1ErrorResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetError().Error.Disposition = runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY
			},
		},
		{
			name: "failure message", result: validRuntimeV1ErrorResult,
			mutate: func(result *runtimev1.BrowserResult) { result.GetError().Error.Message = "secret provider response" },
		},
		{
			name: "failure HTTP status", result: validRuntimeV1ErrorResult,
			mutate: func(result *runtimev1.BrowserResult) {
				status := uint32(503)
				result.GetError().Error.HttpStatus = &status
			},
		},
		{
			name: "failure diagnostics", result: validRuntimeV1ErrorResult,
			mutate: func(result *runtimev1.BrowserResult) {
				result.GetError().Error.DiagnosticDetails = []*runtimev1.DiagnosticDetail{{Key: "secret", Value: "provider"}}
			},
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result := test.result()
			test.mutate(result)
			normalized := sanitizeRuntimeV1Result(result)
			assertRuntimeV1StdioFailure(
				t,
				normalized,
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
			encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(normalized)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(encoded), "secret") || strings.Contains(string(encoded), "provider") {
				t.Fatalf("malformed executor detail escaped: %q", encoded)
			}
		})
	}
}

func TestRuntimeV1StdioOutputIsDeterministicAndBounded(t *testing.T) {
	request := bridgeInput("https://example.test/jobs", "", 0)
	executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	})
	var first, second bytes.Buffer
	if runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &first, executor) != 0 ||
		runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &second, executor) != 0 {
		t.Fatal("deterministic executions failed")
	}
	if !bytes.Equal(first.Bytes(), second.Bytes()) || uint64(first.Len()) > runtimeV1ResultFrameLimit {
		t.Fatalf("outputs differ or exceed bound: %d/%d", first.Len(), second.Len())
	}

	var oversized bytes.Buffer
	code := runRuntimeV1Stdio(
		context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &oversized,
		runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
			return &runtimev1.BrowserResult{
				ContractVersion: strings.Repeat("x", int(runtimeV1ResultFrameLimit)),
				Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
			}
		}),
	)
	if code != 0 || uint64(oversized.Len()) > runtimeV1ResultFrameLimit {
		t.Fatalf("oversized fallback exit/size = %d/%d", code, oversized.Len())
	}
	assertRuntimeV1StdioFailure(
		t,
		decodeRuntimeV1Result(t, oversized.Bytes()),
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
}

func TestRuntimeV1StdioAcceptsAdapterMaximumHTMLWithinOutputBound(t *testing.T) {
	request := bridgeInput("https://example.test/jobs", "", 0)
	html := bytes.Repeat([]byte("x"), int(lightpandaadapter.HTMLPayloadLimit))
	status := uint32(200)
	adapter, err := lightpandaadapter.NewRenderOnly(runtimeV1AdapterRunnerFunc(
		func(_ context.Context, bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
			return lightpandaadapter.NewRunnerSuccess(bound, &lightpandaadapter.RawSuccess{
				FinalURL: request.Plan.TargetUrl,
				Status:   &status,
				HTML:     html,
			})
		},
	))
	if err != nil {
		t.Fatal(err)
	}
	var output bytes.Buffer
	if code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(frameRuntimeV1Input(t, request)), &output, adapter); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	if uint64(output.Len()) > runtimeV1ResultFrameLimit {
		t.Fatalf("maximum adapter result frame = %d, limit = %d", output.Len(), runtimeV1ResultFrameLimit)
	}
	result := decodeRuntimeV1Result(t, output.Bytes())
	if result.GetSuccess() == nil || !bytes.Equal(bridgeManifestBody(result.GetSuccess().Html), html) {
		t.Fatalf("maximum HTML result = %v", result)
	}
}

func TestRuntimeV1StdioWriterFailuresDoNotAppendFallback(t *testing.T) {
	request := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0))
	executor := runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	})
	short := &countingShortWriter{}
	if code := runRuntimeV1Stdio(context.Background(), runtimeV1BorrowedInput(bytes.NewReader(request.Bytes())), short, executor); code != 1 {
		t.Fatalf("short writer exit code = %d", code)
	}
	if short.calls != 1 {
		t.Fatalf("short writer calls = %d, want exactly one without fallback", short.calls)
	}
	if code := runRuntimeV1Stdio(
		context.Background(),
		runtimeV1BorrowedInput(bytes.NewReader(request.Bytes())),
		errorWriter{err: errors.New("closed")},
		executor,
	); code != 1 {
		t.Fatalf("error writer exit code = %d", code)
	}
}

func TestRuntimeV1StdioCancellationInterruptsEveryReadPhase(t *testing.T) {
	complete := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0)).Bytes()
	tests := []struct {
		name string
		data []byte
	}{
		{name: "before frame"},
		{name: "partial frame", data: bytes.Clone(complete[:len(complete)/2])},
		{name: "awaiting EOF", data: bytes.Clone(complete)},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			reader := newInterruptibleBlockingReader(test.data)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			executions := 0
			done := make(chan bytes.Buffer, 1)
			go func() {
				var output bytes.Buffer
				code := runRuntimeV1Stdio(
					ctx,
					runtimeV1OwnedInput(reader),
					&output,
					runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
						executions++
						return nil
					}),
				)
				if code != 0 {
					t.Errorf("exit code = %d", code)
				}
				done <- output
			}()
			select {
			case <-reader.blocked:
			case <-time.After(time.Second):
				t.Fatal("reader did not reach expected blocking phase")
			}
			cancel()
			var output bytes.Buffer
			select {
			case output = <-done:
			case <-time.After(time.Second):
				t.Fatal("cancelled input read did not return")
			}
			select {
			case <-reader.released:
			case <-time.After(time.Second):
				t.Fatal("owned input interrupt was not invoked")
			}
			if executions != 0 || reader.Interruptions() != 1 {
				t.Fatalf("executions/interruptions = %d/%d", executions, reader.Interruptions())
			}
			assertRuntimeV1StdioFailure(
				t,
				decodeRuntimeV1Result(t, output.Bytes()),
				runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
			)
		})
	}
}

func TestRuntimeV1StdioCompletedReadDisarmsInterrupt(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	reader := newInterruptibleBlockingReader(nil)
	reader.block = false
	request := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0))
	reader.data = bytes.Clone(request.Bytes())
	var output bytes.Buffer
	code := runRuntimeV1Stdio(
		ctx,
		runtimeV1OwnedInput(reader),
		&output,
		runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
			return runtimeV1Failure(
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
		}),
	)
	if code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	cancel()
	time.Sleep(10 * time.Millisecond)
	if reader.Interruptions() != 0 {
		t.Fatalf("completed input was interrupted %d times", reader.Interruptions())
	}
}

func TestRuntimeV1StdioBorrowedReaderIsNeverClosed(t *testing.T) {
	request := frameRuntimeV1Input(t, bridgeInput("https://example.test/jobs", "", 0))
	reader := &trackingReadCloser{Reader: bytes.NewReader(request.Bytes())}
	var output bytes.Buffer
	if code := runRuntimeV1Stdio(
		context.Background(),
		runtimeV1BorrowedInput(reader),
		&output,
		runtimeV1ExecutorFunc(func(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult {
			return validRuntimeV1ErrorResult()
		}),
	); code != 0 {
		t.Fatalf("exit code = %d", code)
	}
	if reader.closes != 0 {
		t.Fatalf("borrowed reader was closed %d times", reader.closes)
	}
}

func TestRuntimeV1StdioBuiltBinaryCancelsBlockedInput(t *testing.T) {
	binaryPath := filepath.Join(t.TempDir(), "jobseek-lightpanda-pilot")
	build := exec.Command("go", "build", "-o", binaryPath, ".")
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("build pilot binary: %v\n%s", err, output)
	}
	// Warm the freshly linked executable before timing signal readiness. On
	// Darwin, the first cold start can spend appreciable time in loader and
	// signature work before main installs signal.NotifyContext.
	warmup := exec.Command(binaryPath)
	warmupOutput, warmupErr := warmup.CombinedOutput()
	var warmupExit *exec.ExitError
	if !errors.As(warmupErr, &warmupExit) || warmupExit.ExitCode() != 2 {
		t.Fatalf("warm pilot binary: %v; output=%q", warmupErr, warmupOutput)
	}

	tests := []struct {
		name string
		data []byte
	}{
		{name: "before frame"},
		{name: "partial varint", data: []byte{0x80}},
		{name: "awaiting EOF", data: []byte{0x00}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			command := exec.Command(binaryPath, runtimeV1StdioFlag)
			stdin, err := command.StdinPipe()
			if err != nil {
				t.Fatal(err)
			}
			t.Cleanup(func() { _ = stdin.Close() })
			var stdout, stderr bytes.Buffer
			command.Stdout = &stdout
			command.Stderr = &stderr
			if err := command.Start(); err != nil {
				t.Fatal(err)
			}
			waitDone := make(chan error, 1)
			go func() { waitDone <- command.Wait() }()
			if len(test.data) != 0 {
				if _, err := stdin.Write(test.data); err != nil {
					t.Fatalf("write blocked-input fixture: %v", err)
				}
			}

			// The binary installs its signal context before entering the read.
			// Give a heavily loaded test host time to reach that point so an
			// uncaught default SIGTERM cannot masquerade as graceful shutdown.
			time.Sleep(time.Second)
			if err := command.Process.Signal(syscall.SIGTERM); err != nil {
				t.Fatalf("signal pilot: %v", err)
			}
			select {
			case err := <-waitDone:
				if err != nil {
					t.Fatalf("pilot did not exit cleanly: %v; stderr=%q", err, stderr.String())
				}
			case <-time.After(2 * time.Second):
				_ = command.Process.Kill()
				<-waitDone
				t.Fatal("pilot remained alive after SIGTERM")
			}
			if stderr.Len() != 0 {
				t.Fatalf("pilot stderr = %q", stderr.String())
			}
			assertRuntimeV1StdioFailure(
				t,
				decodeRuntimeV1Result(t, stdout.Bytes()),
				runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
			)
		})
	}
}

func TestRuntimeV1StdioCancellationWaitsForProcessCleanup(t *testing.T) {
	request := bridgeInput("https://example.test/jobs", "", 0)
	events := &eventLog{}
	process := newFakeProcess(events, true)
	started := make(chan struct{})
	config, deps := testRunner(process, taskExecutorFunc(func(ctx context.Context, _ string, _ Task) (Result, error) {
		close(started)
		<-ctx.Done()
		events.add("executor-canceled")
		return Result{}, ctx.Err()
	}), func(int) bool {
		events.add("verify-listener")
		return false
	})
	runner := runtimeV1Runner{
		config: config,
		run: func(ctx context.Context, config Config, task Task) (Result, error) {
			return runTaskWithDependencies(ctx, config, deps, task)
		},
	}
	adapter, err := lightpandaadapter.NewRenderOnly(runner)
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan bytes.Buffer, 1)
	framedRequest := frameRuntimeV1Input(t, request)
	go func() {
		var output bytes.Buffer
		if code := runRuntimeV1Stdio(ctx, runtimeV1BorrowedInput(framedRequest), &output, adapter); code != 0 {
			t.Errorf("exit code = %d", code)
		}
		done <- output
	}()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("runtime-v1 execution did not start")
	}
	cancel()
	var output bytes.Buffer
	select {
	case output = <-done:
	case <-time.After(time.Second):
		t.Fatal("runtime-v1 cancellation did not return after cleanup")
	}
	assertRuntimeV1StdioFailure(
		t,
		decodeRuntimeV1Result(t, output.Bytes()),
		runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
	)
	assertOrdered(t, events.snapshot(), "executor-canceled", "signal-15", "wait-reaped", "verify-listener")
}

func frameRuntimeV1Input(t *testing.T, input *runtimev1.BrowserExecutionInput) *bytes.Buffer {
	t.Helper()
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(input)
	if err != nil {
		t.Fatal(err)
	}
	return bytes.NewBuffer(frameRuntimeV1Payload(t, payload))
}

func frameRuntimeV1Payload(t *testing.T, payload []byte) []byte {
	t.Helper()
	record, err := framing.EncodeRecord(payload, runtimeV1InputFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func decodeRuntimeV1Result(t *testing.T, record []byte) *runtimev1.BrowserResult {
	t.Helper()
	payload, err := framing.DecodeRecord(record, runtimeV1ResultFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	result := &runtimev1.BrowserResult{}
	if err := proto.Unmarshal(payload, result); err != nil {
		t.Fatal(err)
	}
	return result
}

func validRuntimeV1RenderResult(t *testing.T) *runtimev1.BrowserResult {
	t.Helper()
	request := bridgeInput("https://example.test/jobs", "", 0)
	status := uint32(200)
	adapter, err := lightpandaadapter.NewRenderOnly(runtimeV1AdapterRunnerFunc(
		func(_ context.Context, bound lightpandaadapter.BoundInput) lightpandaadapter.RunnerOutcome {
			return lightpandaadapter.NewRunnerSuccess(bound, &lightpandaadapter.RawSuccess{
				FinalURL: request.Plan.TargetUrl,
				Status:   &status,
				HTML:     []byte("<html>rendered</html>"),
			})
		},
	))
	if err != nil {
		t.Fatal(err)
	}
	result := adapter.Execute(context.Background(), request)
	if result.GetSuccess() == nil {
		t.Fatalf("valid render fixture = %v", result)
	}
	return result
}

func validRuntimeV1UnsupportedResult() *runtimev1.BrowserResult {
	return &runtimev1.BrowserResult{
		ContractVersion: "crawler.runtime/v1",
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Unsupported{Unsupported: &runtimev1.BrowserUnsupported{
			Capabilities: []runtimev1.BrowserCapability{
				runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
			},
		}},
	}
}

func validRuntimeV1ErrorResult() *runtimev1.BrowserResult {
	return runtimeV1Failure(
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
}

func assertRuntimeV1StdioFailure(
	t *testing.T,
	result *runtimev1.BrowserResult,
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) {
	t.Helper()
	if result.GetError() == nil || result.GetError().Error == nil ||
		result.GetError().Error.Code != code || result.GetError().Error.Disposition != disposition ||
		result.GetError().Error.Message != "execution failed" ||
		result.GetSuccess() != nil || result.GetUnsupported() != nil {
		t.Fatalf("result = %v, want %s/%s", result, code, disposition)
	}
}

type errorReader struct{ err error }

func (reader errorReader) Read([]byte) (int, error) { return 0, reader.err }

type countingShortWriter struct{ calls int }

func (writer *countingShortWriter) Write(data []byte) (int, error) {
	writer.calls++
	return len(data) - 1, nil
}

type interruptibleBlockingReader struct {
	data          []byte
	block         bool
	blocked       chan struct{}
	released      chan struct{}
	blockedOnce   sync.Once
	interruptOnce sync.Once
	mu            sync.Mutex
	interruptions int
}

func newInterruptibleBlockingReader(data []byte) *interruptibleBlockingReader {
	return &interruptibleBlockingReader{
		data:     bytes.Clone(data),
		block:    true,
		blocked:  make(chan struct{}),
		released: make(chan struct{}),
	}
}

func (reader *interruptibleBlockingReader) Read(output []byte) (int, error) {
	if len(reader.data) != 0 {
		count := copy(output, reader.data)
		reader.data = reader.data[count:]
		return count, nil
	}
	if !reader.block {
		return 0, io.EOF
	}
	reader.blockedOnce.Do(func() { close(reader.blocked) })
	<-reader.released
	return 0, errors.New("input interrupted")
}

func (reader *interruptibleBlockingReader) Interrupt() {
	reader.mu.Lock()
	reader.interruptions++
	reader.mu.Unlock()
	reader.interruptOnce.Do(func() { close(reader.released) })
}

func (reader *interruptibleBlockingReader) Interruptions() int {
	reader.mu.Lock()
	defer reader.mu.Unlock()
	return reader.interruptions
}

func (reader *interruptibleBlockingReader) Close() error {
	reader.Interrupt()
	return nil
}

type trackingReadCloser struct {
	*bytes.Reader
	closes int
}

func (reader *trackingReadCloser) Close() error {
	reader.closes++
	return nil
}
