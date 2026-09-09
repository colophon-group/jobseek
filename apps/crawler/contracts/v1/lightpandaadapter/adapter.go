// Package lightpandaadapter defines the dormant runtime-v1 B1 adapter seam.
//
// It has no process, network, queue, retry, fallback, or persistence authority.
// A caller must inject both the one-shot runner and the evaluation privacy
// boundary.
package lightpandaadapter

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protoreflect"
)

const (
	runtimeContractVersion  = "crawler.runtime/v1"
	evaluationSchemaID      = "jobseek.browser.evaluation-json"
	evaluationSchemaVersion = uint32(1)

	// EvaluationPayloadLimit is the registered evaluation-envelope ceiling.
	EvaluationPayloadLimit uint64 = 64 * 1024
	// HTMLPayloadLimit keeps the inactive adapter's materialized HTML bounded.
	HTMLPayloadLimit uint64 = 1024 * 1024
	// InputPayloadLimit bounds the complete serialized BrowserExecutionInput.
	InputPayloadLimit = 128 * 1024
	// HTMLChunkLimit is the deterministic inline chunk size.
	HTMLChunkLimit         = 64 * 1024
	maxEvaluations         = 1
	maxTargetURLBytes      = 8192
	maxEvaluationIDBytes   = 256
	maxExpressionBytes     = 16 * 1024
	maxNavigationTimeoutMS = 120_000
)

var (
	routingRevisionPattern = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`)
	lowerSHA256Pattern     = regexp.MustCompile(`^[0-9a-f]{64}$`)
)

// Runner is the deliberately unimplemented Lightpanda execution boundary.
// Run is called at most once for one Adapter.Execute invocation. It owns the
// execution and cleanup lifecycle and returns only after cleanup or process
// disposal completes, even when ctx is cancelled.
type Runner interface {
	Run(context.Context, BoundInput) RunnerOutcome
}

// EvaluationPrivacy is the sole authority for canonicalizing and redacting
// one raw evaluation. The adapter supplies the effective result ceiling and
// independently revalidates the returned envelope tuple, size, and digest.
type EvaluationPrivacy interface {
	SealEvaluation(
		context.Context,
		string,
		[]byte,
		uint64,
	) (*runtimev1.ExtensionEnvelope, error)
}

// ProviderFailure is message-free so provider text cannot cross the boundary.
type ProviderFailure struct {
	Code        runtimev1.ErrorCode
	Disposition runtimev1.ErrorDisposition
}

// RawEvaluation is a runner-produced JSON value awaiting mandatory privacy.
type RawEvaluation struct {
	EvaluationID string
	JSON         []byte
}

// RawSuccess is the only B1 provider success shape. Status and HTML are
// required for every admitted B1 plan; a non-nil empty HTML slice means a
// present empty document.
type RawSuccess struct {
	FinalURL    string
	Status      *uint32
	HTML        []byte
	Evaluations []RawEvaluation
}

type runnerOutcomeKind uint8

const (
	runnerOutcomeInvalid runnerOutcomeKind = iota
	runnerOutcomeSuccess
	runnerOutcomeFailure
	runnerOutcomeCleanupFailure
	runnerOutcomeResourceLimit
)

// RunnerOutcome is opaque. Runners construct it through the functions below,
// which bind the input identity and clone every caller-owned value.
type RunnerOutcome struct {
	kind               runnerOutcomeKind
	bindingFingerprint [sha256.Size]byte
	success            *RawSuccess
	failure            ProviderFailure
}

// NewRunnerSuccess clones a bounded raw result after the runner has completed
// cleanup. Oversized material is represented without copying it and is mapped
// to the closed resource-limit result by Execute.
func NewRunnerSuccess(bound BoundInput, raw *RawSuccess) RunnerOutcome {
	if raw == nil || len(raw.FinalURL) > maxTargetURLBytes || len(raw.Evaluations) > maxEvaluations {
		return RunnerOutcome{bindingFingerprint: bound.fingerprint}
	}
	if len(raw.Evaluations) == 1 && len(raw.Evaluations[0].EvaluationID) > maxEvaluationIDBytes {
		return RunnerOutcome{bindingFingerprint: bound.fingerprint}
	}
	if uint64(len(raw.HTML)) > HTMLPayloadLimit ||
		(len(raw.Evaluations) == 1 && uint64(len(raw.Evaluations[0].JSON)) > EvaluationPayloadLimit) {
		return RunnerOutcome{
			kind: runnerOutcomeResourceLimit, bindingFingerprint: bound.fingerprint,
		}
	}
	cloned := &RawSuccess{
		FinalURL: raw.FinalURL,
		HTML:     append([]byte(nil), raw.HTML...),
	}
	if raw.HTML != nil && cloned.HTML == nil {
		cloned.HTML = []byte{}
	}
	if raw.Status != nil {
		status := *raw.Status
		cloned.Status = &status
	}
	if len(raw.Evaluations) == 1 {
		cloned.Evaluations = []RawEvaluation{{
			EvaluationID: raw.Evaluations[0].EvaluationID,
			JSON:         append([]byte(nil), raw.Evaluations[0].JSON...),
		}}
		if raw.Evaluations[0].JSON != nil && cloned.Evaluations[0].JSON == nil {
			cloned.Evaluations[0].JSON = []byte{}
		}
	}
	return RunnerOutcome{
		kind: runnerOutcomeSuccess, bindingFingerprint: bound.fingerprint, success: cloned,
	}
}

// NewRunnerFailure copies a typed provider failure after cleanup completes.
func NewRunnerFailure(bound BoundInput, failure ProviderFailure) RunnerOutcome {
	return RunnerOutcome{
		kind: runnerOutcomeFailure, bindingFingerprint: bound.fingerprint, failure: failure,
	}
}

// NewRunnerCleanupFailure reports that cleanup or process disposal could not
// be proved. It is authoritative over concurrent timeout/cancellation.
func NewRunnerCleanupFailure(bound BoundInput) RunnerOutcome {
	return RunnerOutcome{
		kind: runnerOutcomeCleanupFailure, bindingFingerprint: bound.fingerprint,
	}
}

// BoundInput retains an immutable full clone. Every accessor returns another
// clone, so an injected runner cannot mutate the adapter's binding.
type BoundInput struct {
	input       *runtimev1.BrowserExecutionInput
	fingerprint [sha256.Size]byte
}

// Input returns an isolated clone of the complete bound input.
func (bound BoundInput) Input() *runtimev1.BrowserExecutionInput {
	if bound.input == nil {
		return nil
	}
	return proto.Clone(bound.input).(*runtimev1.BrowserExecutionInput)
}

// Fingerprint returns SHA-256 of deterministic protobuf bytes for the complete
// BrowserExecutionInput clone.
func (bound BoundInput) Fingerprint() [sha256.Size]byte { return bound.fingerprint }

// Adapter is an inactive in-process contract seam. It owns no goroutine or
// external lifecycle.
type Adapter struct {
	runner  Runner
	privacy EvaluationPrivacy
}

// New rejects missing dependencies rather than providing implicit behavior.
func New(runner Runner, privacy EvaluationPrivacy) (*Adapter, error) {
	if runner == nil || privacy == nil {
		return nil, errors.New("lightpanda adapter requires runner and evaluation privacy")
	}
	return &Adapter{runner: runner, privacy: privacy}, nil
}

// Execute validates and binds before one runner call. There is no retry,
// fallback, or asynchronous wrapper. Every post-run failure discards all
// partially mapped output.
func (adapter *Adapter) Execute(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) *runtimev1.BrowserResult {
	if adapter == nil || adapter.runner == nil || adapter.privacy == nil {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if ctx == nil {
		ctx = context.Background()
	}

	bound, unsupported, ok := bind(input)
	if !ok {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		)
	}
	if len(unsupported) != 0 {
		return unsupportedResult(unsupported)
	}
	if err := ctx.Err(); err != nil {
		return contextFailureResult(err)
	}

	runnerCtx, cancelRunner := context.WithTimeout(
		ctx, time.Duration(bound.input.Plan.Navigation.TimeoutMs)*time.Millisecond,
	)
	outcome, runnerPanicked := runOnce(adapter.runner, runnerCtx, bound)
	runnerContextErr := runnerCtx.Err()
	cancelRunner()
	if runnerPanicked || outcome.bindingFingerprint != bound.fingerprint ||
		outcome.kind == runnerOutcomeInvalid {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if outcome.kind == runnerOutcomeCleanupFailure {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if runnerContextErr != nil {
		return contextFailureResult(runnerContextErr)
	}
	if outcome.kind == runnerOutcomeResourceLimit {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
	}
	if outcome.kind == runnerOutcomeFailure {
		if !validFailurePair(outcome.failure.Code, outcome.failure.Disposition) {
			return failureResult(
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
		}
		return failureResult(outcome.failure.Code, outcome.failure.Disposition)
	}
	if outcome.kind != runnerOutcomeSuccess {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}

	success, limitExceeded, valid := adapter.mapSuccess(ctx, bound.input.Plan, outcome.success)
	if err := ctx.Err(); err != nil {
		return contextFailureResult(err)
	}
	if limitExceeded {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
	}
	if !valid {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersion,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome:         &runtimev1.BrowserResult_Success{Success: success},
	}
}

func bind(input *runtimev1.BrowserExecutionInput) (BoundInput, []runtimev1.BrowserCapability, bool) {
	if !shallowB1Cardinalities(input) || proto.Size(input) > InputPayloadLimit ||
		hasUnknownFields(input.ProtoReflect()) {
		return BoundInput{}, nil, false
	}
	cloned := proto.Clone(input).(*runtimev1.BrowserExecutionInput)
	assignment := cloned.Assignment
	if assignment.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA ||
		assignment.CapabilityClass != runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION ||
		assignment.ServiceLane != runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA ||
		!routingRevisionPattern.MatchString(assignment.RoutingRevision) {
		return BoundInput{}, nil, false
	}

	_, unsupported, valid := validateCapabilities(cloned.Plan.RequiredCapabilities)
	if !valid {
		return BoundInput{}, nil, false
	}
	if len(unsupported) != 0 {
		return BoundInput{}, unsupported, true
	}
	if !validB1Plan(cloned.Plan) {
		return BoundInput{}, nil, false
	}
	encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(cloned)
	if err != nil {
		return BoundInput{}, nil, false
	}
	return BoundInput{input: cloned, fingerprint: sha256.Sum256(encoded)}, nil, true
}

// shallowB1Cardinalities executes before reflection, cloning, hashing, or any
// recursive validation. It makes all later collection walks constant-bounded.
func shallowB1Cardinalities(input *runtimev1.BrowserExecutionInput) bool {
	if input == nil || input.Plan == nil || input.Assignment == nil {
		return false
	}
	plan := input.Plan
	if len(plan.RequiredCapabilities) == 0 ||
		len(plan.RequiredCapabilities) > int(runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES) ||
		plan.Navigation == nil || len(plan.Navigation.Headers) != 0 || plan.Session != nil ||
		len(plan.Actions) != 0 || len(plan.Captures) != 0 || len(plan.Evaluations) > maxEvaluations ||
		len(plan.Interceptions) != 0 || len(plan.OriginOperations) != 1 {
		return false
	}
	return true
}

func validateCapabilities(
	capabilities []runtimev1.BrowserCapability,
) ([]runtimev1.BrowserCapability, []runtimev1.BrowserCapability, bool) {
	if len(capabilities) < 1 {
		return nil, nil, false
	}
	seen := make(map[runtimev1.BrowserCapability]bool, len(capabilities))
	normalized := append([]runtimev1.BrowserCapability(nil), capabilities...)
	for _, capability := range normalized {
		if capability <= runtimev1.BrowserCapability_BROWSER_CAPABILITY_UNSPECIFIED ||
			capability > runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES ||
			seen[capability] {
			return nil, nil, false
		}
		seen[capability] = true
	}
	if !seen[runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER] {
		return nil, nil, false
	}
	unsupported := make([]runtimev1.BrowserCapability, 0, len(normalized))
	for capability := range seen {
		if capability != runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER &&
			capability != runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE {
			unsupported = append(unsupported, capability)
		}
	}
	sort.Slice(unsupported, func(left, right int) bool { return unsupported[left] < unsupported[right] })
	sort.Slice(normalized, func(left, right int) bool { return normalized[left] < normalized[right] })
	return normalized, unsupported, true
}

func validB1Plan(plan *runtimev1.BrowserPlan) bool {
	if plan == nil || plan.ContractVersion != runtimeContractVersion || !validHTTPURL(plan.TargetUrl) ||
		plan.Navigation == nil || plan.Session != nil || len(plan.Actions) != 0 ||
		len(plan.Captures) != 0 || len(plan.Interceptions) != 0 ||
		len(plan.Evaluations) > maxEvaluations ||
		len(plan.OriginOperations) != 1 {
		return false
	}
	navigation := plan.Navigation
	if navigation.WaitUntil != runtimev1.WaitCondition_WAIT_CONDITION_LOAD ||
		navigation.TimeoutMs == 0 || navigation.TimeoutMs > maxNavigationTimeoutMS ||
		len(navigation.Headers) != 0 || navigation.IgnoreTlsErrors || navigation.OriginRequestId == "" {
		return false
	}
	hasEvaluate := false
	for _, capability := range plan.RequiredCapabilities {
		if capability == runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE {
			hasEvaluate = true
		}
	}
	if hasEvaluate != (len(plan.Evaluations) == 1) {
		return false
	}
	operation := plan.OriginOperations[0]
	if operation == nil || operation.OriginRequestId != navigation.OriginRequestId ||
		operation.OperationSequence != 1 || operation.Role != "navigation" ||
		operation.ParentOriginRequestId != nil || !lowerSHA256Pattern.MatchString(operation.RequestFingerprint) {
		return false
	}
	seenEvaluations := make(map[string]bool, len(plan.Evaluations))
	for _, evaluation := range plan.Evaluations {
		if evaluation == nil || evaluation.EvaluationId == "" ||
			len(evaluation.EvaluationId) > maxEvaluationIDBytes || seenEvaluations[evaluation.EvaluationId] ||
			evaluation.Expression == "" || len(evaluation.Expression) > maxExpressionBytes ||
			evaluation.MaxResultBytes == 0 || evaluation.FrameName != nil ||
			evaluation.NetworkEffect != runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_NONE ||
			evaluation.OriginRequestId != nil {
			return false
		}
		seenEvaluations[evaluation.EvaluationId] = true
	}
	return true
}

func runOnce(
	runner Runner,
	ctx context.Context,
	bound BoundInput,
) (outcome RunnerOutcome, panicked bool) {
	defer func() {
		if recover() != nil {
			outcome = RunnerOutcome{}
			panicked = true
		}
	}()
	return runner.Run(ctx, bound), false
}

func validHTTPURL(value string) bool {
	if value == "" || len(value) > maxTargetURLBytes || !utf8.ValidString(value) ||
		strings.ContainsAny(value, "\r\n") {
		return false
	}
	parsed, err := url.ParseRequestURI(value)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" ||
		parsed.User != nil || parsed.Fragment != "" {
		return false
	}
	return true
}

func hasUnknownFields(message protoreflect.Message) bool {
	if !message.IsValid() || len(message.GetUnknown()) != 0 {
		return true
	}
	unknown := false
	message.Range(func(field protoreflect.FieldDescriptor, value protoreflect.Value) bool {
		if field.IsMap() {
			if field.MapValue().Kind() != protoreflect.MessageKind &&
				field.MapValue().Kind() != protoreflect.GroupKind {
				return true
			}
			value.Map().Range(func(_ protoreflect.MapKey, item protoreflect.Value) bool {
				unknown = hasUnknownFields(item.Message())
				return !unknown
			})
			return !unknown
		}
		if field.IsList() {
			if field.Kind() != protoreflect.MessageKind && field.Kind() != protoreflect.GroupKind {
				return true
			}
			items := value.List()
			for index := 0; index < items.Len(); index++ {
				if hasUnknownFields(items.Get(index).Message()) {
					unknown = true
					return false
				}
			}
			return true
		}
		if field.Kind() == protoreflect.MessageKind || field.Kind() == protoreflect.GroupKind {
			unknown = hasUnknownFields(value.Message())
		}
		return !unknown
	})
	return unknown
}

func (adapter *Adapter) mapSuccess(
	ctx context.Context,
	plan *runtimev1.BrowserPlan,
	raw *RawSuccess,
) (*runtimev1.BrowserSuccess, bool, bool) {
	if raw == nil || !validHTTPURL(raw.FinalURL) || raw.Status == nil ||
		*raw.Status < 100 || *raw.Status > 599 || raw.HTML == nil ||
		len(raw.Evaluations) != len(plan.Evaluations) {
		return nil, false, false
	}
	if uint64(len(raw.HTML)) > HTMLPayloadLimit {
		return nil, true, false
	}

	for index, value := range raw.Evaluations {
		planned := plan.Evaluations[index]
		if value.EvaluationID != planned.EvaluationId {
			return nil, false, false
		}
		effectiveLimit := planned.MaxResultBytes
		if effectiveLimit > EvaluationPayloadLimit {
			effectiveLimit = EvaluationPayloadLimit
		}
		if uint64(len(value.JSON)) > effectiveLimit {
			return nil, true, false
		}
	}

	evaluations := make([]*runtimev1.EvaluationValue, 0, len(raw.Evaluations))
	for index, value := range raw.Evaluations {
		planned := plan.Evaluations[index]
		effectiveLimit := planned.MaxResultBytes
		if effectiveLimit > EvaluationPayloadLimit {
			effectiveLimit = EvaluationPayloadLimit
		}
		rawJSON := append([]byte(nil), value.JSON...)
		envelope, err, panicked := sealOnce(
			adapter.privacy,
			ctx, value.EvaluationID, rawJSON, effectiveLimit,
		)
		if err != nil || panicked || !validEvaluationEnvelope(envelope, effectiveLimit) {
			return nil, false, false
		}
		evaluations = append(evaluations, &runtimev1.EvaluationValue{
			EvaluationId: value.EvaluationID,
			Value:        proto.Clone(envelope).(*runtimev1.ExtensionEnvelope),
		})
	}

	statusValue := *raw.Status
	return &runtimev1.BrowserSuccess{
		FinalUrl:    raw.FinalURL,
		Status:      &statusValue,
		Html:        inlineChunkManifest(raw.HTML),
		Evaluations: evaluations,
	}, false, true
}

func sealOnce(
	privacy EvaluationPrivacy,
	ctx context.Context,
	evaluationID string,
	raw []byte,
	limit uint64,
) (envelope *runtimev1.ExtensionEnvelope, err error, panicked bool) {
	defer func() {
		if recover() != nil {
			envelope = nil
			err = errors.New("lightpanda evaluation privacy panicked")
			panicked = true
		}
	}()
	envelope, err = privacy.SealEvaluation(ctx, evaluationID, raw, limit)
	return envelope, err, false
}

func validEvaluationEnvelope(envelope *runtimev1.ExtensionEnvelope, limit uint64) bool {
	if envelope == nil || hasUnknownFields(envelope.ProtoReflect()) ||
		envelope.SchemaId != evaluationSchemaID ||
		envelope.SchemaVersion != evaluationSchemaVersion ||
		envelope.Encoding != runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON ||
		uint64(len(envelope.Payload)) > limit || !json.Valid(envelope.Payload) ||
		!lowerSHA256Pattern.MatchString(envelope.PayloadSha256) {
		return false
	}
	digest := sha256.Sum256(envelope.Payload)
	return hex.EncodeToString(digest[:]) == envelope.PayloadSha256
}

func inlineChunkManifest(content []byte) *runtimev1.ChunkManifest {
	totalDigest := sha256.Sum256(content)
	manifest := &runtimev1.ChunkManifest{
		TotalSizeBytes: uint64(len(content)),
		TotalSha256:    hex.EncodeToString(totalDigest[:]),
		Complete:       true,
	}
	for start, sequence := 0, uint32(0); start < len(content); start, sequence = start+HTMLChunkLimit, sequence+1 {
		end := start + HTMLChunkLimit
		if end > len(content) {
			end = len(content)
		}
		body := append([]byte(nil), content[start:end]...)
		digest := sha256.Sum256(body)
		manifest.Chunks = append(manifest.Chunks, &runtimev1.DataChunk{
			Sequence:  sequence,
			SizeBytes: uint64(len(body)),
			Sha256:    hex.EncodeToString(digest[:]),
			Storage:   &runtimev1.DataChunk_InlineBody{InlineBody: body},
		})
	}
	return manifest
}

func validFailurePair(code runtimev1.ErrorCode, disposition runtimev1.ErrorDisposition) bool {
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

func contextFailureResult(err error) *runtimev1.BrowserResult {
	if errors.Is(err, context.DeadlineExceeded) {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		)
	}
	return failureResult(
		runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
	)
}

func failureResult(
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) *runtimev1.BrowserResult {
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersion,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Error{Error: &runtimev1.BrowserFailure{
			Error: &runtimev1.RuntimeError{
				Code:        code,
				Disposition: disposition,
				Message:     "lightpanda execution failed",
			},
		}},
	}
}

func unsupportedResult(capabilities []runtimev1.BrowserCapability) *runtimev1.BrowserResult {
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersion,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA,
		Outcome: &runtimev1.BrowserResult_Unsupported{Unsupported: &runtimev1.BrowserUnsupported{
			Capabilities: append([]runtimev1.BrowserCapability(nil), capabilities...),
		}},
	}
}
