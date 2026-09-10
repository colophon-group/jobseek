package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/url"
	"strings"
	"unicode/utf8"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
)

const (
	runtimeV1StdioFlag = "--runtime-v1-stdio"
	// InputPayloadLimit is 128 KiB; its canonical prefix is three bytes.
	runtimeV1InputFrameLimit = uint64(lightpandaadapter.InputPayloadLimit + 3)
	// BrowserResult is materialized before it is written. This cap leaves
	// bounded protobuf overhead above the adapter's HTML/evaluation ceilings.
	runtimeV1ResultFrameLimit = uint64(2 * lightpandaadapter.HTMLPayloadLimit)
)

type runtimeV1Executor interface {
	Execute(context.Context, *runtimev1.BrowserExecutionInput) *runtimev1.BrowserResult
}

// runtimeV1Input makes cancellation ownership explicit. Interrupt requests
// best-effort release of an owned Reader after ctx ends; it is never applied
// to borrowed input. The one-shot binary may exit while a Darwin pipe Close
// is still blocked behind Read. Borrowed input must be nonblocking. Reusable
// callers with blocking owned input must provide an interrupt that returns
// promptly if they need input goroutine cleanup without process exit.
type runtimeV1Input struct {
	Reader    io.Reader
	Interrupt func()
}

func runtimeV1BorrowedInput(reader io.Reader) runtimeV1Input {
	return runtimeV1Input{Reader: reader}
}

func runtimeV1OwnedInput(reader io.ReadCloser) runtimeV1Input {
	if reader == nil {
		return runtimeV1Input{}
	}
	return runtimeV1Input{
		Reader: reader,
		Interrupt: func() {
			_ = reader.Close()
		},
	}
}

// runRuntimeV1Stdio consumes exactly one request record and produces exactly
// one response record. It is deliberately a one-shot handler, not a session
// protocol or service loop.
func runRuntimeV1Stdio(
	ctx context.Context,
	input runtimeV1Input,
	output io.Writer,
	executor runtimeV1Executor,
) int {
	request, result := readRuntimeV1InputContext(ctx, input)
	if result == nil {
		if executor == nil {
			result = runtimeV1Failure(
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
		} else {
			result = executor.Execute(ctx, request)
		}
	}
	result = sanitizeRuntimeV1Result(result)
	if err := writeRuntimeV1Result(output, result); err != nil {
		return 1
	}
	return 0
}

func runRuntimeV1StdioMode(
	ctx context.Context,
	args []string,
	input runtimeV1Input,
	output io.Writer,
	executor runtimeV1Executor,
) int {
	if len(args) != 1 || args[0] != runtimeV1StdioFlag {
		if err := writeRuntimeV1Result(output, runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		)); err != nil {
			return 1
		}
		return 0
	}
	return runRuntimeV1Stdio(ctx, input, output, executor)
}

func readRuntimeV1InputContext(
	ctx context.Context,
	input runtimeV1Input,
) (*runtimev1.BrowserExecutionInput, *runtimev1.BrowserResult) {
	if ctx == nil {
		ctx = context.Background()
	}
	if err := ctx.Err(); err != nil {
		return nil, runtimeV1ContextFailure(err)
	}
	if input.Interrupt == nil {
		request, result := readRuntimeV1Input(input.Reader)
		if err := ctx.Err(); err != nil {
			return nil, runtimeV1ContextFailure(err)
		}
		return request, result
	}

	type readOutcome struct {
		request *runtimev1.BrowserExecutionInput
		result  *runtimev1.BrowserResult
	}
	readDone := make(chan readOutcome, 1)
	go func() {
		request, result := readRuntimeV1Input(input.Reader)
		readDone <- readOutcome{request: request, result: result}
	}()

	select {
	case outcome := <-readDone:
		if err := ctx.Err(); err != nil {
			return nil, runtimeV1ContextFailure(err)
		}
		return outcome.request, outcome.result
	case <-ctx.Done():
		// This is a one-shot process protocol. On Darwin, closing a pipe-backed
		// os.Stdin from another goroutine can itself wait behind the blocked
		// Read. Invoke the owned-input interrupt asynchronously and return the
		// cancellation record immediately; main exits after the single write,
		// which releases any blocked read/close goroutines. Library callers that
		// supply blocking inputs must likewise provide an interrupt that returns
		// promptly if they need goroutine cleanup before process exit.
		if input.Interrupt != nil {
			go func() {
				defer func() { _ = recover() }()
				input.Interrupt()
			}()
		}
		return nil, runtimeV1ContextFailure(ctx.Err())
	}
}

func readRuntimeV1Input(input io.Reader) (*runtimev1.BrowserExecutionInput, *runtimev1.BrowserResult) {
	if input == nil {
		return nil, runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	payload, err := framing.ReadRecord(input, runtimeV1InputFrameLimit)
	if err != nil {
		return nil, runtimeV1ReadFailure(err)
	}
	if err := requireRuntimeV1EOF(input); err != nil {
		return nil, runtimeV1ReadFailure(err)
	}
	request := &runtimev1.BrowserExecutionInput{}
	if err := proto.Unmarshal(payload, request); err != nil {
		return nil, runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		)
	}
	return request, nil
}

func runtimeV1ContextFailure(err error) *runtimev1.BrowserResult {
	if errors.Is(err, context.DeadlineExceeded) {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		)
	}
	return runtimeV1Failure(
		runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
	)
}

func runtimeV1ReadFailure(err error) *runtimev1.BrowserResult {
	if errors.Is(err, framing.ErrFrameLimit) {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
	}
	var framingErr *framing.FramingError
	if errors.As(err, &framingErr) || isRuntimeV1FramingFailure(err) ||
		errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		)
	}
	return runtimeV1Failure(
		runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	)
}

func isRuntimeV1FramingFailure(err error) bool {
	return errors.Is(err, framing.ErrNonminimalPrefix) ||
		errors.Is(err, framing.ErrPrefixOverflow) ||
		errors.Is(err, framing.ErrTruncatedPrefix) ||
		errors.Is(err, framing.ErrTruncatedPayload) ||
		errors.Is(err, framing.ErrTrailingBytes) ||
		errors.Is(err, framing.ErrAmbiguousEOF) ||
		errors.Is(err, framing.ErrReaderContract)
}

func requireRuntimeV1EOF(input io.Reader) error {
	var trailing [1]byte
	count, err := input.Read(trailing[:])
	if count < 0 || count > len(trailing) || (count == 0 && err == nil) {
		return framing.ErrReaderContract
	}
	if count != 0 {
		return framing.ErrTrailingBytes
	}
	if errors.Is(err, io.EOF) {
		return nil
	}
	return err
}

func writeRuntimeV1Result(output io.Writer, result *runtimev1.BrowserResult) error {
	if output == nil {
		return errors.New("runtime-v1 output is unavailable")
	}
	record, err := marshalRuntimeV1Record(result)
	if err != nil {
		code := runtimev1.ErrorCode_ERROR_CODE_INTERNAL
		disposition := runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY
		if errors.Is(err, framing.ErrFrameLimit) {
			code = runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT
			disposition = runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY
		}
		fallback := runtimeV1Failure(
			code,
			disposition,
		)
		record, err = marshalRuntimeV1Record(fallback)
		if err != nil {
			return errors.New("runtime-v1 fallback exceeded its output bound")
		}
	}
	written, err := output.Write(record)
	if err != nil {
		return errors.New("runtime-v1 result could not be written")
	}
	if written != len(record) {
		return io.ErrShortWrite
	}
	return nil
}

func marshalRuntimeV1Record(result *runtimev1.BrowserResult) ([]byte, error) {
	size := proto.Size(result)
	if size < 0 {
		return nil, errors.New("runtime-v1 result has an invalid size")
	}
	unsignedSize := uint64(size)
	prefixSize := framing.UvarintSize(unsignedSize)
	if runtimeV1ResultFrameLimit < prefixSize || unsignedSize > runtimeV1ResultFrameLimit-prefixSize {
		return nil, framing.ErrFrameLimit
	}
	payload, err := proto.MarshalOptions{Deterministic: true}.Marshal(result)
	if err != nil {
		return nil, errors.New("runtime-v1 result could not be encoded")
	}
	return framing.EncodeRecord(payload, runtimeV1ResultFrameLimit)
}

func sanitizeRuntimeV1Result(result *runtimev1.BrowserResult) *runtimev1.BrowserResult {
	invalid := func() *runtimev1.BrowserResult {
		return runtimeV1Failure(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if result == nil || result.ContractVersion != "crawler.runtime/v1" ||
		result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA ||
		len(result.ProtoReflect().GetUnknown()) != 0 {
		return invalid()
	}

	switch outcome := result.Outcome.(type) {
	case *runtimev1.BrowserResult_Success:
		if outcome == nil || !validRuntimeV1RenderSuccess(outcome.Success) {
			return invalid()
		}
		return &runtimev1.BrowserResult{
			ContractVersion: "crawler.runtime/v1",
			Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
			Outcome: &runtimev1.BrowserResult_Success{
				Success: proto.Clone(outcome.Success).(*runtimev1.BrowserSuccess),
			},
		}
	case *runtimev1.BrowserResult_Unsupported:
		if outcome == nil || !validRuntimeV1Unsupported(outcome.Unsupported) {
			return invalid()
		}
		return &runtimev1.BrowserResult{
			ContractVersion: "crawler.runtime/v1",
			Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
			Outcome: &runtimev1.BrowserResult_Unsupported{Unsupported: &runtimev1.BrowserUnsupported{
				Capabilities: append([]runtimev1.BrowserCapability(nil), outcome.Unsupported.Capabilities...),
			}},
		}
	case *runtimev1.BrowserResult_Error:
		if outcome == nil || !validRuntimeV1Failure(outcome.Error) {
			return invalid()
		}
		return runtimeV1Failure(outcome.Error.Error.Code, outcome.Error.Error.Disposition)
	default:
		return invalid()
	}
}

func validRuntimeV1RenderSuccess(success *runtimev1.BrowserSuccess) bool {
	if success == nil || len(success.ProtoReflect().GetUnknown()) != 0 ||
		!validRuntimeV1URL(success.FinalUrl) || success.Status == nil ||
		*success.Status < 100 || *success.Status > 599 || success.Html == nil ||
		len(success.ActionOutcomes) != 0 || len(success.Captures) != 0 ||
		len(success.Evaluations) != 0 || len(success.Artifacts) != 0 {
		return false
	}
	return validRuntimeV1HTML(success.Html)
}

func validRuntimeV1HTML(manifest *runtimev1.ChunkManifest) bool {
	if manifest == nil || len(manifest.ProtoReflect().GetUnknown()) != 0 || !manifest.Complete ||
		manifest.TotalSizeBytes > lightpandaadapter.HTMLPayloadLimit ||
		!validRuntimeV1SHA256(manifest.TotalSha256) {
		return false
	}
	expectedChunks := uint64(0)
	if manifest.TotalSizeBytes != 0 {
		expectedChunks = (manifest.TotalSizeBytes + lightpandaadapter.HTMLChunkLimit - 1) /
			lightpandaadapter.HTMLChunkLimit
	}
	if uint64(len(manifest.Chunks)) != expectedChunks {
		return false
	}

	totalHash := sha256.New()
	remaining := manifest.TotalSizeBytes
	for index, chunk := range manifest.Chunks {
		if chunk == nil || len(chunk.ProtoReflect().GetUnknown()) != 0 ||
			chunk.Sequence != uint32(index) || !validRuntimeV1SHA256(chunk.Sha256) {
			return false
		}
		expectedSize := uint64(lightpandaadapter.HTMLChunkLimit)
		if remaining < expectedSize {
			expectedSize = remaining
		}
		inline, ok := chunk.Storage.(*runtimev1.DataChunk_InlineBody)
		if !ok || inline == nil || chunk.SizeBytes != expectedSize ||
			uint64(len(inline.InlineBody)) != expectedSize {
			return false
		}
		chunkDigest := sha256.Sum256(inline.InlineBody)
		if chunk.Sha256 != hex.EncodeToString(chunkDigest[:]) {
			return false
		}
		_, _ = totalHash.Write(inline.InlineBody)
		remaining -= expectedSize
	}
	return remaining == 0 && manifest.TotalSha256 == hex.EncodeToString(totalHash.Sum(nil))
}

func validRuntimeV1Unsupported(unsupported *runtimev1.BrowserUnsupported) bool {
	if unsupported == nil || len(unsupported.ProtoReflect().GetUnknown()) != 0 ||
		len(unsupported.Capabilities) == 0 ||
		len(unsupported.Capabilities) > int(runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES) ||
		len(unsupported.DiagnosticArtifacts) != 0 {
		return false
	}
	previous := runtimev1.BrowserCapability_BROWSER_CAPABILITY_UNSPECIFIED
	for _, capability := range unsupported.Capabilities {
		if capability <= previous || capability == runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER ||
			capability > runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES {
			return false
		}
		previous = capability
	}
	return true
}

func validRuntimeV1Failure(failure *runtimev1.BrowserFailure) bool {
	if failure == nil || len(failure.ProtoReflect().GetUnknown()) != 0 || failure.Error == nil ||
		len(failure.DiagnosticArtifacts) != 0 || len(failure.Error.ProtoReflect().GetUnknown()) != 0 ||
		failure.Error.HttpStatus != nil || failure.Error.RetryAfterMs != nil ||
		len(failure.Error.DiagnosticDetails) != 0 ||
		(failure.Error.Message != "execution failed" && failure.Error.Message != "lightpanda execution failed") {
		return false
	}
	return validRuntimeV1FailurePair(failure.Error.Code, failure.Error.Disposition)
}

func validRuntimeV1FailurePair(
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) bool {
	switch code {
	case runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
		runtimev1.ErrorCode_ERROR_CODE_TARGET_LOST,
		runtimev1.ErrorCode_ERROR_CODE_SESSION_LOST,
		runtimev1.ErrorCode_ERROR_CODE_TRANSPORT,
		runtimev1.ErrorCode_ERROR_CODE_NAVIGATION:
		return disposition == runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT:
		return disposition == runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_CANCELLED:
		return disposition == runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG:
		return disposition == runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY
	case runtimev1.ErrorCode_ERROR_CODE_INTERNAL:
		return disposition == runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY
	default:
		return false
	}
}

func validRuntimeV1URL(value string) bool {
	if value == "" || len(value) > maxURLBytes || !utf8.ValidString(value) || strings.ContainsAny(value, "\r\n") {
		return false
	}
	parsed, err := url.ParseRequestURI(value)
	return err == nil && (parsed.Scheme == "http" || parsed.Scheme == "https") &&
		parsed.Host != "" && parsed.User == nil && parsed.Fragment == ""
}

func validRuntimeV1SHA256(value string) bool {
	if len(value) != sha256.Size*2 {
		return false
	}
	for _, unit := range value {
		if (unit < '0' || unit > '9') && (unit < 'a' || unit > 'f') {
			return false
		}
	}
	return true
}

func runtimeV1Failure(
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) *runtimev1.BrowserResult {
	return &runtimev1.BrowserResult{
		ContractVersion: "crawler.runtime/v1",
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Error{Error: &runtimev1.BrowserFailure{
			Error: &runtimev1.RuntimeError{
				Code:        code,
				Disposition: disposition,
				Message:     "execution failed",
			},
		}},
	}
}
