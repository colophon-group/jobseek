package adjacentpolicy

// This file is a candidate-only, standard-library control-state validator.
// It deliberately has no crawler runtime, persistence, or network authority.

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"time"
)

const (
	controlFormat   = "jobseek.runtime.control-corpus/v1"
	contractVersion = "crawler.runtime/v1"
)

var mandatoryControlCaseIDs = map[string]bool{
	"accept_complete":                           true,
	"accept_deduplicated_bound_resume":          true,
	"accept_identical_unacked_replay":           true,
	"accept_resume_after_ambiguous_dispatch":    true,
	"reject_actual_limit_exceeded":              true,
	"reject_deadline_expired_history":           true,
	"reject_divergent_sequence_reuse":           true,
	"reject_duplicate_logical_dedup":            true,
	"reject_duplicate_origin_dispatch":          true,
	"reject_fault_metadata_mismatch":            true,
	"reject_frame_after_terminal":               true,
	"reject_late_frame_after_cancel":            true,
	"reject_manifest_revision_changed":          true,
	"reject_origin_dispatch_before_declaration": true,
	"reject_origin_identity_reused":             true,
	"reject_origin_redeclaration_changed":       true,
	"reject_request_binding_changed":            true,
	"reject_reused_attempt":                     true,
	"reject_sequence_gap":                       true,
	"reject_sequence_rewind":                    true,
	"reject_stale_fence":                        true,
	"reject_terminal_count_mismatch":            true,
	"reject_terminal_duplicate":                 true,
	"reject_terminal_missing":                   true,
	"reject_trace_binding_changed":              true,
	"reject_unknown_checkpoint":                 true,
	"reject_unknown_origin_contact":             true,
}

var controlErrorCodes = map[string]bool{
	"ok":                                  true,
	"active_duration_limit_exceeded":      true,
	"artifact_count_limit_exceeded":       true,
	"artifact_identity_reused":            true,
	"artifact_total_bytes_limit_exceeded": true,
	"binding_changed":                     true,
	"cancelled":                           true,
	"credit_exceeded":                     true,
	"deadline_exceeded":                   true,
	"deadline_regression":                 true,
	"divergent_sequence_reuse":            true,
	"duplicate_logical_dedup":             true,
	"duplicate_origin_dispatch":           true,
	"error_local_cap_exceeded":            true,
	"fault_metadata_mismatch":             true,
	"frame_after_terminal":                true,
	"frame_limit_exceeded":                true,
	"frame_size_limit_exceeded":           true,
	"fixture_cut_mismatch":                true,
	"fixture_injection_phase_mismatch":    true,
	"initial_origin_parent_unknown":       true,
	"initial_origin_sequence_invalid":     true,
	"invalid_corpus":                      true,
	"invalid_deadline":                    true,
	"invalid_trace_context":               true,
	"limits_changed":                      true,
	"manifest_revision_changed":           true,
	"origin_deduplication_not_ambiguous":  true,
	"origin_dispatch_before_declaration":  true,
	"origin_fingerprint_changed":          true,
	"origin_identity_reused":              true,
	"origin_local_cap_exceeded":           true,
	"origin_redeclaration_changed":        true,
	"output_limit_exceeded":               true,
	"reused_attempt":                      true,
	"resume_handshake_missing":            true,
	"sequence_gap":                        true,
	"sequence_rewind":                     true,
	"stale_fence":                         true,
	"terminal_count_mismatch":             true,
	"terminal_duplicate":                  true,
	"terminal_missing":                    true,
	"trace_binding_changed":               true,
	"transport_invalidated":               true,
	"unknown_checkpoint":                  true,
	"unknown_origin_contact":              true,
	"wrong_frame_kind":                    true,
}

// These are fixture-machine safety caps, not negotiated Limits fields.
const (
	localMaxOriginOperations = 4
	localMaxErrors           = 4
)

var runtimeErrorCodes = map[string]bool{
	"anti_bot":               true,
	"ambiguous_origin":       true,
	"cancelled":              true,
	"empty_result":           true,
	"http_status":            true,
	"internal":               true,
	"invalid_config":         true,
	"navigation":             true,
	"permanent_gone":         true,
	"provider_gone":          true,
	"resource_limit":         true,
	"session_lost":           true,
	"target_lost":            true,
	"tdm_reserved":           true,
	"timeout":                true,
	"transport":              true,
	"unsupported_capability": true,
}

var (
	traceparentPattern = regexp.MustCompile(`^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$`)
	tracestateKey      = regexp.MustCompile(`^(?:[a-z][a-z0-9_\-*/]{0,255}|[a-z0-9][a-z0-9_\-*/]{0,240}@[a-z][a-z0-9_\-*/]{0,13})$`)
	originIDPattern    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}/origin/[0-9]{4,10}$`)
	hex64Pattern       = regexp.MustCompile(`^[0-9a-f]{64}$`)
	rfc3339Pattern     = regexp.MustCompile(`^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})$`)
)

type protocolFailure struct{ code string }

func (failure protocolFailure) Error() string { return failure.code }

func fail(code string) error {
	if !controlErrorCodes[code] {
		panic("unregistered control error code: " + code)
	}
	return protocolFailure{code: code}
}

func failureCode(err error) string {
	var failure protocolFailure
	if errors.As(err, &failure) {
		return failure.code
	}
	return "invalid_corpus"
}

type controlCorpus struct {
	Cases           []controlCase `json:"cases"`
	Format          string        `json:"format"`
	RequiredCaseIDs []string      `json:"required_case_ids"`
}

type expectedResult struct {
	Accepted bool   `json:"accepted"`
	Code     string `json:"code"`
}

type controlCase struct {
	Events   []controlEvent `json:"events"`
	Expected expectedResult `json:"expected"`
	ID       string         `json:"id"`
	Metadata caseMetadata   `json:"metadata"`
}

type caseMetadata struct {
	DurableCutEventIndex uint64 `json:"durable_cut_event_index"`
	InjectionPhase       string `json:"injection_phase"`
	LogicalTime          string `json:"logical_time_rfc3339"`
}

type limitsShape struct {
	MaxActiveDurationMS   uint64 `json:"max_active_duration_ms"`
	MaxArtifactChunkBytes uint64 `json:"max_artifact_chunk_bytes"`
	MaxArtifactCount      uint64 `json:"max_artifact_count"`
	MaxArtifactTotalBytes uint64 `json:"max_artifact_total_bytes"`
	MaxBrowserActions     uint64 `json:"max_browser_actions"`
	MaxBrowserCaptures    uint64 `json:"max_browser_captures"`
	MaxBrowserEvaluations uint64 `json:"max_browser_evaluations"`
	MaxBrowserTransfer    uint64 `json:"max_browser_transfer_bytes"`
	MaxExecutionFrames    uint64 `json:"max_execution_frames"`
	MaxFrameBytes         uint64 `json:"max_frame_bytes"`
	MaxHTTPTransfer       uint64 `json:"max_http_transfer_bytes"`
	MaxInFlightFrames     uint64 `json:"max_in_flight_frames"`
	MaxInlineBodyBytes    uint64 `json:"max_inline_body_bytes"`
	MaxMonitorBatches     uint64 `json:"max_monitor_batches"`
	MaxOutputItems        uint64 `json:"max_output_items"`
	MaxRetryAfterMS       uint64 `json:"max_retry_after_ms"`
}

type manifestShapeV1 struct {
	ConfigFingerprint string `json:"config_fingerprint"`
	ConfigRevision    string `json:"config_revision"`
	ManifestID        string `json:"manifest_id"`
}

type fenceShape struct {
	ClaimToken     string `json:"claim_token"`
	ConfigRevision string `json:"config_revision"`
	EngineOwner    string `json:"engine_owner"`
	FenceDigest    string `json:"fence_digest"`
	LeaseID        string `json:"lease_id"`
	RoutingEpoch   uint64 `json:"routing_epoch"`
	ShardID        string `json:"shard_id"`
}

type operationShape struct {
	OperationSequence     uint64  `json:"operation_sequence"`
	OriginRequestID       string  `json:"origin_request_id"`
	ParentOriginRequestID *string `json:"parent_origin_request_id,omitempty"`
	RequestFingerprint    string  `json:"request_fingerprint"`
	Role                  string  `json:"role"`
}

type requestShape struct {
	AttemptID        string                     `json:"attempt_id"`
	BoardManifest    manifestShapeV1            `json:"board_manifest"`
	ContractVersion  string                     `json:"contract_version"`
	Deadline         string                     `json:"deadline_rfc3339"`
	FencingContext   fenceShape                 `json:"fencing_context"`
	Input            map[string]json.RawMessage `json:"input"`
	Kind             string                     `json:"kind"`
	OriginOperations []operationShape           `json:"origin_operations"`
	OriginRequestID  string                     `json:"origin_request_id"`
	RequestID        string                     `json:"request_id"`
	Traceparent      *string                    `json:"traceparent,omitempty"`
	Tracestate       *string                    `json:"tracestate,omitempty"`
}

type resumeShape struct {
	AfterSequence   *uint64    `json:"after_sequence,omitempty"`
	AttemptID       string     `json:"attempt_id"`
	ContractVersion string     `json:"contract_version"`
	FencingContext  fenceShape `json:"fencing_context"`
	OriginRequestID string     `json:"origin_request_id"`
	RequestID       string     `json:"request_id"`
}

type windowShape struct {
	AdditionalFrames uint64 `json:"additional_frames"`
	AttemptID        string `json:"attempt_id"`
	FenceDigest      string `json:"fence_digest"`
	RequestID        string `json:"request_id"`
}

type cancelShape struct {
	AttemptID      string     `json:"attempt_id"`
	FencingContext fenceShape `json:"fencing_context"`
	Reason         string     `json:"reason"`
	RequestID      string     `json:"request_id"`
}

type clientHelloShape struct {
	Implementation            string      `json:"implementation"`
	RequestedLimits           limitsShape `json:"requested_limits"`
	SupportedContractVersions []string    `json:"supported_contract_versions"`
}

type serverHelloShape struct {
	AcceptedAt              string      `json:"accepted_at_rfc3339"`
	AcceptedLimits          limitsShape `json:"accepted_limits"`
	Implementation          string      `json:"implementation"`
	InitialWindowFrames     uint64      `json:"initial_window_frames"`
	ResumeByOriginRequestID bool        `json:"resume_by_origin_request_id"`
	SelectedContractVersion string      `json:"selected_contract_version"`
}

type faultShape struct {
	OriginRequestID     string  `json:"origin_request_id"`
	OriginWasDispatched bool    `json:"origin_was_dispatched"`
	Point               string  `json:"point"`
	RequestFingerprint  string  `json:"request_fingerprint"`
	Sequence            *uint64 `json:"sequence,omitempty"`
}

type artifactShape struct {
	Handle    string `json:"handle"`
	SizeBytes uint64 `json:"size_bytes"`
}

type payloadShape struct {
	ActiveDurationMS     *uint64         `json:"active_duration_ms,omitempty"`
	Artifact             *artifactShape  `json:"artifact,omitempty"`
	ArtifactCount        *uint64         `json:"artifact_count,omitempty"`
	Code                 *string         `json:"code,omitempty"`
	Disposition          *string         `json:"disposition,omitempty"`
	EligibleForCommit    *bool           `json:"eligible_for_commit,omitempty"`
	FrameCount           *uint64         `json:"frame_count,omitempty"`
	MonitorBatches       *uint64         `json:"monitor_batches,omitempty"`
	Operation            *operationShape `json:"operation,omitempty"`
	OriginOperationCount *uint64         `json:"origin_operation_count,omitempty"`
	OutputItems          *uint64         `json:"output_items,omitempty"`
	RequestFingerprint   *string         `json:"request_fingerprint,omitempty"`
	Status               *string         `json:"status,omitempty"`
	Type                 string          `json:"type"`
}

type frameShape struct {
	AttemptID       string       `json:"attempt_id"`
	ContractVersion string       `json:"contract_version"`
	FenceDigest     string       `json:"fence_digest"`
	Payload         payloadShape `json:"payload"`
	RequestID       string       `json:"request_id"`
	Sequence        uint64       `json:"sequence"`
}

type measurementsShape struct {
	OutputItems   *uint64 `json:"output_items,omitempty"`
	WireSizeBytes uint64  `json:"wire_size_bytes"`
}

type controlEvent struct {
	Cancel       *cancelShape       `json:"cancel,omitempty"`
	ClientHello  *clientHelloShape  `json:"client_hello,omitempty"`
	Direction    string             `json:"direction"`
	Fault        *faultShape        `json:"fault,omitempty"`
	Frame        *frameShape        `json:"frame,omitempty"`
	Measurements *measurementsShape `json:"measurements,omitempty"`
	Resume       *resumeShape       `json:"resume,omitempty"`
	ServerHello  *serverHelloShape  `json:"server_hello,omitempty"`
	Start        *requestShape      `json:"start,omitempty"`
	WindowUpdate *windowShape       `json:"window_update,omitempty"`
}

type ledgerEntry struct {
	Operation        operationShape
	State            string
	DispatchSequence *uint64
	DedupSequence    *uint64
}

type ledgerResult struct {
	OperationSequence  uint64 `json:"operation_sequence"`
	OriginRequestID    string `json:"origin_request_id"`
	RequestFingerprint string `json:"request_fingerprint"`
	State              string `json:"state"`
}

type resultCounts struct {
	ArtifactBytes  uint64 `json:"artifact_bytes"`
	Artifacts      uint64 `json:"artifacts"`
	Declared       uint64 `json:"declared"`
	Deduplicated   uint64 `json:"deduplicated"`
	Dispatched     uint64 `json:"dispatched"`
	Errors         uint64 `json:"errors"`
	Frames         uint64 `json:"frames"`
	MonitorBatches uint64 `json:"monitor_batches"`
	Outputs        uint64 `json:"outputs"`
	ReplayedFrames uint64 `json:"replayed_frames"`
}

type terminalResult struct {
	ActiveDurationMS     uint64 `json:"active_duration_ms"`
	ArtifactCount        uint64 `json:"artifact_count"`
	EligibleForCommit    bool   `json:"eligible_for_commit"`
	FrameCount           uint64 `json:"frame_count"`
	MonitorBatches       uint64 `json:"monitor_batches"`
	OriginOperationCount uint64 `json:"origin_operation_count"`
	OutputItems          uint64 `json:"output_items"`
	Status               string `json:"status"`
}

type controlResult struct {
	Accepted      bool            `json:"accepted"`
	BindingSHA256 string          `json:"binding_sha256"`
	CaseID        string          `json:"case_id"`
	Code          string          `json:"code"`
	Counts        resultCounts    `json:"counts"`
	Ledger        []ledgerResult  `json:"ledger"`
	Terminal      *terminalResult `json:"terminal"`
}

type validatorState struct {
	caseID                  string
	request                 requestShape
	hasRequest              bool
	limits                  limitsShape
	acceptedAt              *time.Time
	logicalTime             time.Time
	durableCut              uint64
	initialWindow           uint64
	bindingSHA256           string
	currentAttempt          string
	attempts                map[string]bool
	credit                  uint64
	requestedLimits         *limitsShape
	pendingLimits           *limitsShape
	pendingWindow           *uint64
	pendingAcceptedAt       *time.Time
	ledger                  map[string]*ledgerEntry
	operationSequences      map[uint64]bool
	artifactHandles         map[string]bool
	counts                  resultCounts
	history                 map[uint64]string
	lastSequence            int64
	replayCursor            *uint64
	replayTo                uint64
	highestAcknowledged     int64
	terminal                *terminalResult
	cancelled               bool
	transportInvalidated    bool
	resumed                 bool
	lastPayloadType         string
	lastResultSequence      *uint64
	lastPhysicalSequence    int64
	lastPhysicalPayloadType string
}

func decodeStrict(content []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return errors.New("multiple JSON values")
	}
	return nil
}

func parseRFC3339(value string) (time.Time, error) {
	if !rfc3339Pattern.MatchString(value) {
		return time.Time{}, fail("invalid_deadline")
	}
	parsed, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return time.Time{}, fail("invalid_deadline")
	}
	_, offset := parsed.Zone()
	if offset > 14*60*60 || offset < -14*60*60 {
		return time.Time{}, fail("invalid_deadline")
	}
	return parsed.UTC(), nil
}

func validateTrace(parent *string, state *string) error {
	if parent == nil {
		if state != nil {
			return fail("invalid_trace_context")
		}
		return nil
	}
	match := traceparentPattern.FindStringSubmatch(*parent)
	if match == nil || strings.Trim(match[1], "0") == "" || strings.Trim(match[2], "0") == "" {
		return fail("invalid_trace_context")
	}
	if state == nil {
		return nil
	}
	if len(*state) == 0 || len(*state) > 512 {
		return fail("invalid_trace_context")
	}
	members := strings.Split(*state, ",")
	if len(members) > 32 {
		return fail("invalid_trace_context")
	}
	keys := map[string]bool{}
	for _, member := range members {
		if strings.TrimSpace(member) != member || strings.Count(member, "=") != 1 {
			return fail("invalid_trace_context")
		}
		parts := strings.SplitN(member, "=", 2)
		key, value := parts[0], parts[1]
		if !tracestateKey.MatchString(key) || keys[key] || len(value) == 0 || len(value) > 256 || strings.TrimSpace(value) != value {
			return fail("invalid_trace_context")
		}
		for _, character := range []byte(value) {
			if character < 0x20 || character > 0x7e || character == ',' || character == '=' {
				return fail("invalid_trace_context")
			}
		}
		keys[key] = true
	}
	return nil
}

func validateFence(value fenceShape) error {
	if value.ClaimToken == "" || value.ConfigRevision == "" || value.LeaseID == "" || value.ShardID == "" || value.RoutingEpoch == 0 {
		return fail("invalid_corpus")
	}
	if value.EngineOwner != "python" && value.EngineOwner != "go" {
		return fail("invalid_corpus")
	}
	if !hex64Pattern.MatchString(value.FenceDigest) {
		return fail("invalid_corpus")
	}
	return nil
}

func validateOperation(value operationShape) error {
	if value.OperationSequence == 0 || value.Role == "" || !originIDPattern.MatchString(value.OriginRequestID) || !hex64Pattern.MatchString(value.RequestFingerprint) {
		return fail("invalid_corpus")
	}
	if value.ParentOriginRequestID != nil && !originIDPattern.MatchString(*value.ParentOriginRequestID) {
		return fail("invalid_corpus")
	}
	return nil
}

func validateLimits(value limitsShape) error {
	values := []uint64{
		value.MaxActiveDurationMS,
		value.MaxArtifactChunkBytes,
		value.MaxArtifactCount,
		value.MaxArtifactTotalBytes,
		value.MaxBrowserActions,
		value.MaxBrowserCaptures,
		value.MaxBrowserEvaluations,
		value.MaxBrowserTransfer,
		value.MaxExecutionFrames,
		value.MaxFrameBytes,
		value.MaxHTTPTransfer,
		value.MaxInFlightFrames,
		value.MaxInlineBodyBytes,
		value.MaxMonitorBatches,
		value.MaxOutputItems,
		value.MaxRetryAfterMS,
	}
	for _, item := range values {
		if item == 0 {
			return fail("invalid_corpus")
		}
	}
	if value.MaxInlineBodyBytes > value.MaxFrameBytes {
		return fail("invalid_corpus")
	}
	return nil
}

func validateRequest(value requestShape) error {
	if value.ContractVersion != contractVersion || value.RequestID == "" || value.AttemptID == "" || value.OriginRequestID == "" {
		return fail("invalid_corpus")
	}
	if value.Kind != "monitor" && value.Kind != "scrape" && value.Kind != "browser" {
		return fail("invalid_corpus")
	}
	if _, err := parseRFC3339(value.Deadline); err != nil {
		return err
	}
	if err := validateTrace(value.Traceparent, value.Tracestate); err != nil {
		return err
	}
	if value.BoardManifest.ConfigFingerprint == "" || value.BoardManifest.ConfigRevision == "" || value.BoardManifest.ManifestID == "" {
		return fail("invalid_corpus")
	}
	if err := validateFence(value.FencingContext); err != nil {
		return err
	}
	if len(value.Input) != 1 || value.Input[value.Kind] == nil {
		return fail("invalid_corpus")
	}
	if len(value.OriginOperations) == 0 {
		return fail("invalid_corpus")
	}
	seenOperationIDs := map[string]bool{}
	for index, operation := range value.OriginOperations {
		if err := validateOperation(operation); err != nil {
			return err
		}
		if operation.OperationSequence != uint64(index+1) {
			return fail("initial_origin_sequence_invalid")
		}
		if operation.ParentOriginRequestID != nil && !seenOperationIDs[*operation.ParentOriginRequestID] {
			return fail("initial_origin_parent_unknown")
		}
		seenOperationIDs[operation.OriginRequestID] = true
	}
	if value.OriginRequestID != value.OriginOperations[0].OriginRequestID {
		return fail("invalid_corpus")
	}
	return nil
}

func canonicalBinding(request requestShape, limits limitsShape) (string, error) {
	requestBytes, err := json.Marshal(request)
	if err != nil {
		return "", err
	}
	var requestMap map[string]any
	decoder := json.NewDecoder(bytes.NewReader(requestBytes))
	decoder.UseNumber()
	if err := decoder.Decode(&requestMap); err != nil {
		return "", err
	}
	delete(requestMap, "attempt_id")
	limitBytes, err := json.Marshal(limits)
	if err != nil {
		return "", err
	}
	var limitMap map[string]any
	decoder = json.NewDecoder(bytes.NewReader(limitBytes))
	decoder.UseNumber()
	if err := decoder.Decode(&limitMap); err != nil {
		return "", err
	}
	canonical, err := json.Marshal(map[string]any{
		"negotiated_limits": limitMap,
		"request":           requestMap,
	})
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(canonical)
	return hex.EncodeToString(digest[:]), nil
}

func newValidator(item controlCase) (*validatorState, error) {
	if item.ID == "" {
		return nil, fail("invalid_corpus")
	}
	logicalTime, err := parseRFC3339(item.Metadata.LogicalTime)
	if err != nil {
		return nil, err
	}
	state := &validatorState{
		caseID:               item.ID,
		attempts:             map[string]bool{},
		ledger:               map[string]*ledgerEntry{},
		operationSequences:   map[uint64]bool{},
		artifactHandles:      map[string]bool{},
		history:              map[uint64]string{},
		lastSequence:         -1,
		logicalTime:          logicalTime,
		durableCut:           item.Metadata.DurableCutEventIndex,
		highestAcknowledged:  -1,
		lastPhysicalSequence: -1,
	}
	return state, nil
}

func (state *validatorState) result(accepted bool, code string) controlResult {
	ledger := make([]ledgerResult, 0, len(state.ledger))
	for _, entry := range state.ledger {
		ledger = append(ledger, ledgerResult{
			OperationSequence:  entry.Operation.OperationSequence,
			OriginRequestID:    entry.Operation.OriginRequestID,
			RequestFingerprint: entry.Operation.RequestFingerprint,
			State:              entry.State,
		})
	}
	sort.Slice(ledger, func(left, right int) bool {
		return ledger[left].OperationSequence < ledger[right].OperationSequence
	})
	counts := state.counts
	counts.Declared = uint64(len(state.ledger))
	return controlResult{
		Accepted:      accepted,
		BindingSHA256: state.bindingSHA256,
		CaseID:        state.caseID,
		Code:          code,
		Counts:        counts,
		Ledger:        ledger,
		Terminal:      state.terminal,
	}
}

func emptyFailure(caseID, code string) controlResult {
	return controlResult{
		Accepted: false,
		CaseID:   caseID,
		Code:     code,
		Ledger:   []ledgerResult{},
	}
}

func (state *validatorState) deadlineExpired() bool {
	if !state.hasRequest {
		return false
	}
	deadline, err := parseRFC3339(state.request.Deadline)
	return err == nil && state.logicalTime.After(deadline)
}

func (state *validatorState) checkFence(value fenceShape) error {
	expected := state.request.FencingContext
	if reflect.DeepEqual(value, expected) {
		return nil
	}
	if value.RoutingEpoch < expected.RoutingEpoch || value.FenceDigest != expected.FenceDigest {
		return fail("stale_fence")
	}
	return fail("binding_changed")
}

func withoutAttempt(value requestShape) requestShape {
	value.AttemptID = ""
	return value
}

func (state *validatorState) checkStartBinding(checkpoint requestShape) error {
	original := state.request
	if checkpoint.BoardManifest.ConfigRevision != original.BoardManifest.ConfigRevision {
		return fail("manifest_revision_changed")
	}
	originalDeadline, err := parseRFC3339(original.Deadline)
	if err != nil {
		return err
	}
	checkpointDeadline, err := parseRFC3339(checkpoint.Deadline)
	if err != nil {
		return err
	}
	if checkpointDeadline.Before(originalDeadline) {
		return fail("deadline_regression")
	}
	if !reflect.DeepEqual(checkpoint.Traceparent, original.Traceparent) || !reflect.DeepEqual(checkpoint.Tracestate, original.Tracestate) {
		return fail("trace_binding_changed")
	}
	if !reflect.DeepEqual(checkpoint.FencingContext, original.FencingContext) {
		if checkpoint.FencingContext.RoutingEpoch < original.FencingContext.RoutingEpoch || checkpoint.FencingContext.FenceDigest != original.FencingContext.FenceDigest {
			return fail("stale_fence")
		}
		return fail("binding_changed")
	}
	if !reflect.DeepEqual(withoutAttempt(checkpoint), withoutAttempt(original)) {
		return fail("binding_changed")
	}
	return nil
}

func (state *validatorState) handleClientHello(hello clientHelloShape) error {
	if state.requestedLimits != nil || state.pendingLimits != nil {
		return fail("invalid_corpus")
	}
	if hello.Implementation != "python" && hello.Implementation != "go" {
		return fail("invalid_corpus")
	}
	if len(hello.SupportedContractVersions) != 1 || hello.SupportedContractVersions[0] != contractVersion {
		return fail("invalid_corpus")
	}
	if err := validateLimits(hello.RequestedLimits); err != nil {
		return err
	}
	copyOfLimits := hello.RequestedLimits
	state.requestedLimits = &copyOfLimits
	return nil
}

func limitsWithin(accepted, requested limitsShape) bool {
	return accepted.MaxActiveDurationMS <= requested.MaxActiveDurationMS &&
		accepted.MaxArtifactChunkBytes <= requested.MaxArtifactChunkBytes &&
		accepted.MaxArtifactCount <= requested.MaxArtifactCount &&
		accepted.MaxArtifactTotalBytes <= requested.MaxArtifactTotalBytes &&
		accepted.MaxBrowserActions <= requested.MaxBrowserActions &&
		accepted.MaxBrowserCaptures <= requested.MaxBrowserCaptures &&
		accepted.MaxBrowserEvaluations <= requested.MaxBrowserEvaluations &&
		accepted.MaxBrowserTransfer <= requested.MaxBrowserTransfer &&
		accepted.MaxExecutionFrames <= requested.MaxExecutionFrames &&
		accepted.MaxFrameBytes <= requested.MaxFrameBytes &&
		accepted.MaxHTTPTransfer <= requested.MaxHTTPTransfer &&
		accepted.MaxInFlightFrames <= requested.MaxInFlightFrames &&
		accepted.MaxInlineBodyBytes <= requested.MaxInlineBodyBytes &&
		accepted.MaxMonitorBatches <= requested.MaxMonitorBatches &&
		accepted.MaxOutputItems <= requested.MaxOutputItems &&
		accepted.MaxRetryAfterMS <= requested.MaxRetryAfterMS
}

func (state *validatorState) handleServerHello(hello serverHelloShape) error {
	if state.requestedLimits == nil || state.pendingLimits != nil || hello.SelectedContractVersion != contractVersion || (hello.Implementation != "python" && hello.Implementation != "go") || !hello.ResumeByOriginRequestID {
		return fail("invalid_corpus")
	}
	if err := validateLimits(hello.AcceptedLimits); err != nil {
		return err
	}
	if !limitsWithin(hello.AcceptedLimits, *state.requestedLimits) || hello.InitialWindowFrames == 0 || hello.InitialWindowFrames > hello.AcceptedLimits.MaxInFlightFrames {
		return fail("invalid_corpus")
	}
	acceptedAt, err := parseRFC3339(hello.AcceptedAt)
	if err != nil {
		return err
	}
	copyOfLimits := hello.AcceptedLimits
	copyOfWindow := hello.InitialWindowFrames
	state.pendingLimits = &copyOfLimits
	state.pendingWindow = &copyOfWindow
	state.pendingAcceptedAt = &acceptedAt
	return nil
}

func (state *validatorState) handleStart(candidate requestShape) error {
	if err := validateRequest(candidate); err != nil {
		return err
	}
	if state.hasRequest {
		if err := state.checkStartBinding(candidate); err != nil {
			return err
		}
		return fail("binding_changed")
	}
	if state.pendingLimits == nil || state.pendingWindow == nil || state.pendingAcceptedAt == nil {
		return fail("invalid_corpus")
	}
	deadline, err := parseRFC3339(candidate.Deadline)
	if err != nil {
		return err
	}
	if !deadline.After(*state.pendingAcceptedAt) {
		return fail("invalid_deadline")
	}
	if uint64(deadline.Sub(*state.pendingAcceptedAt)/time.Millisecond) > state.pendingLimits.MaxActiveDurationMS {
		return fail("active_duration_limit_exceeded")
	}
	binding, err := canonicalBinding(candidate, *state.pendingLimits)
	if err != nil {
		return fail("invalid_corpus")
	}
	state.request = candidate
	state.hasRequest = true
	state.limits = *state.pendingLimits
	state.initialWindow = *state.pendingWindow
	acceptedAt := *state.pendingAcceptedAt
	state.acceptedAt = &acceptedAt
	if state.logicalTime.Before(acceptedAt) {
		return fail("invalid_deadline")
	}
	state.bindingSHA256 = binding
	state.currentAttempt = candidate.AttemptID
	state.attempts[candidate.AttemptID] = true
	state.credit = state.initialWindow
	for _, operation := range candidate.OriginOperations {
		if state.ledger[operation.OriginRequestID] != nil || state.operationSequences[operation.OperationSequence] {
			return fail("origin_identity_reused")
		}
		copyOfOperation := operation
		state.ledger[operation.OriginRequestID] = &ledgerEntry{Operation: copyOfOperation, State: "declared"}
		state.operationSequences[operation.OperationSequence] = true
		if len(state.ledger) > localMaxOriginOperations {
			return fail("origin_local_cap_exceeded")
		}
	}
	state.requestedLimits = nil
	state.pendingLimits = nil
	state.pendingWindow = nil
	state.pendingAcceptedAt = nil
	return nil
}

func (state *validatorState) handleResume(resume resumeShape) error {
	if !state.hasRequest || state.pendingLimits == nil || state.pendingWindow == nil || state.pendingAcceptedAt == nil {
		return fail("resume_handshake_missing")
	}
	if !reflect.DeepEqual(*state.pendingLimits, state.limits) {
		return fail("limits_changed")
	}
	if resume.ContractVersion != contractVersion || resume.RequestID != state.request.RequestID || resume.OriginRequestID != state.request.OriginRequestID {
		return fail("binding_changed")
	}
	if err := validateFence(resume.FencingContext); err != nil {
		return err
	}
	if err := state.checkFence(resume.FencingContext); err != nil {
		return err
	}
	if resume.AttemptID == "" || state.attempts[resume.AttemptID] {
		return fail("reused_attempt")
	}
	after := int64(-1)
	if resume.AfterSequence != nil {
		if *resume.AfterSequence > uint64(^uint64(0)>>1) {
			return fail("invalid_corpus")
		}
		after = int64(*resume.AfterSequence)
	}
	if after < state.highestAcknowledged {
		return fail("sequence_rewind")
	}
	if after > state.lastSequence {
		return fail("unknown_checkpoint")
	}
	state.currentAttempt = resume.AttemptID
	state.attempts[resume.AttemptID] = true
	if after < state.lastSequence {
		cursor := uint64(after + 1)
		state.replayCursor = &cursor
	} else {
		state.replayCursor = nil
	}
	state.replayTo = uint64(state.lastSequence)
	state.credit = *state.pendingWindow
	state.resumed = true
	state.transportInvalidated = false
	state.highestAcknowledged = after
	state.requestedLimits = nil
	state.pendingLimits = nil
	state.pendingWindow = nil
	state.pendingAcceptedAt = nil
	return nil
}

func (state *validatorState) handleWindow(update windowShape) error {
	if update.RequestID != state.request.RequestID || update.AttemptID != state.currentAttempt || update.FenceDigest != state.request.FencingContext.FenceDigest {
		return fail("stale_fence")
	}
	if update.AdditionalFrames == 0 || update.AdditionalFrames > state.limits.MaxInFlightFrames-state.credit {
		return fail("credit_exceeded")
	}
	state.credit += update.AdditionalFrames
	return nil
}

func (state *validatorState) handleCancel(cancel cancelShape) error {
	if cancel.RequestID != state.request.RequestID || cancel.AttemptID != state.currentAttempt || cancel.Reason == "" {
		return fail("binding_changed")
	}
	if err := validateFence(cancel.FencingContext); err != nil {
		return err
	}
	if err := state.checkFence(cancel.FencingContext); err != nil {
		return err
	}
	state.cancelled = true
	return nil
}

func (state *validatorState) handleClient(event controlEvent) error {
	present := 0
	if event.ClientHello != nil {
		present++
	}
	if event.Start != nil {
		present++
	}
	if event.Resume != nil {
		present++
	}
	if event.WindowUpdate != nil {
		present++
	}
	if event.Cancel != nil {
		present++
	}
	if present != 1 || event.Frame != nil || event.Fault != nil || event.ServerHello != nil || event.Measurements != nil {
		return fail("invalid_corpus")
	}
	if state.transportInvalidated && event.ClientHello == nil && event.Resume == nil {
		return fail("transport_invalidated")
	}
	if event.ClientHello != nil {
		return state.handleClientHello(*event.ClientHello)
	}
	if event.Start != nil {
		return state.handleStart(*event.Start)
	}
	if event.Resume != nil {
		return state.handleResume(*event.Resume)
	}
	if event.WindowUpdate != nil {
		return state.handleWindow(*event.WindowUpdate)
	}
	return state.handleCancel(*event.Cancel)
}

func (state *validatorState) faultOperation(fault faultShape) (*ledgerEntry, error) {
	if !fault.OriginWasDispatched {
		if fault.OriginRequestID != "" || fault.RequestFingerprint != "" {
			return nil, fail("fault_metadata_mismatch")
		}
		return nil, nil
	}
	entry := state.ledger[fault.OriginRequestID]
	if entry == nil || (entry.State != "dispatched" && entry.State != "ambiguous") || fault.RequestFingerprint != entry.Operation.RequestFingerprint || entry.DispatchSequence == nil {
		return nil, fail("fault_metadata_mismatch")
	}
	return entry, nil
}

func (state *validatorState) handleFault(event controlEvent) error {
	if state.transportInvalidated {
		return fail("transport_invalidated")
	}
	if event.Fault == nil || event.Frame != nil || event.Resume != nil || event.WindowUpdate != nil || event.Cancel != nil || event.ClientHello != nil || event.ServerHello != nil || event.Start != nil || event.Measurements != nil {
		return fail("invalid_corpus")
	}
	fault := *event.Fault
	if state.cancelled || state.deadlineExpired() {
		return fail("fault_metadata_mismatch")
	}
	entry, err := state.faultOperation(fault)
	if err != nil {
		return err
	}
	matchesSequence := func(expected int64) bool {
		return fault.Sequence == nil || int64(*fault.Sequence) == expected
	}
	switch fault.Point {
	case "after_dispatch":
		if entry == nil || int64(*entry.DispatchSequence) != state.lastPhysicalSequence || state.lastPhysicalPayloadType != "origin_contact" || !matchesSequence(state.lastPhysicalSequence) {
			return fail("fault_metadata_mismatch")
		}
	case "before_frame":
		if !matchesSequence(state.lastPhysicalSequence + 1) {
			return fail("fault_metadata_mismatch")
		}
	case "after_frame":
		if state.lastPhysicalSequence < 0 || !matchesSequence(state.lastPhysicalSequence) {
			return fail("fault_metadata_mismatch")
		}
	case "result_before_terminal":
		if state.lastPhysicalSequence < 0 || !matchesSequence(state.lastPhysicalSequence) || (state.lastPhysicalPayloadType != "monitor_batch" && state.lastPhysicalPayloadType != "scrape_result" && state.lastPhysicalPayloadType != "browser_result") || entry != nil {
			return fail("fault_metadata_mismatch")
		}
	default:
		return fail("invalid_corpus")
	}
	if entry != nil {
		entry.State = "ambiguous"
	}
	state.transportInvalidated = true
	return nil
}

func frameSignature(frame frameShape, measurements measurementsShape) (string, error) {
	value := map[string]any{
		"contract_version": frame.ContractVersion,
		"fence_digest":     frame.FenceDigest,
		"payload":          frame.Payload,
		"request_id":       frame.RequestID,
		"sequence":         frame.Sequence,
		"measurements":     measurements,
	}
	content, err := json.Marshal(value)
	return string(content), err
}

func (state *validatorState) addDynamicOperation(operation operationShape) error {
	if err := validateOperation(operation); err != nil {
		return err
	}
	if existing := state.ledger[operation.OriginRequestID]; existing != nil {
		if reflect.DeepEqual(existing.Operation, operation) {
			return fail("origin_identity_reused")
		}
		return fail("origin_redeclaration_changed")
	}
	if state.operationSequences[operation.OperationSequence] {
		return fail("origin_identity_reused")
	}
	maximum := uint64(0)
	for sequence := range state.operationSequences {
		if sequence > maximum {
			maximum = sequence
		}
	}
	if operation.OperationSequence != maximum+1 {
		return fail("origin_identity_reused")
	}
	if operation.ParentOriginRequestID != nil && state.ledger[*operation.ParentOriginRequestID] == nil {
		return fail("origin_identity_reused")
	}
	copyOfOperation := operation
	state.ledger[operation.OriginRequestID] = &ledgerEntry{Operation: copyOfOperation, State: "declared"}
	state.operationSequences[operation.OperationSequence] = true
	if len(state.ledger) > localMaxOriginOperations {
		return fail("origin_local_cap_exceeded")
	}
	return nil
}

func (state *validatorState) originContact(payload payloadShape, sequence uint64) error {
	if payload.Operation == nil || payload.RequestFingerprint == nil || payload.Disposition == nil {
		return fail("invalid_corpus")
	}
	operation := *payload.Operation
	if err := validateOperation(operation); err != nil {
		return err
	}
	entry := state.ledger[operation.OriginRequestID]
	if entry == nil {
		if operation.OperationSequence != uint64(len(state.operationSequences)+1) {
			return fail("unknown_origin_contact")
		}
		return fail("origin_dispatch_before_declaration")
	}
	if operation.RequestFingerprint != entry.Operation.RequestFingerprint || *payload.RequestFingerprint != entry.Operation.RequestFingerprint {
		return fail("origin_fingerprint_changed")
	}
	if !reflect.DeepEqual(operation, entry.Operation) {
		return fail("origin_redeclaration_changed")
	}
	switch *payload.Disposition {
	case "dispatched":
		if entry.State != "declared" {
			return fail("duplicate_origin_dispatch")
		}
		entry.State = "dispatched"
		copyOfSequence := sequence
		entry.DispatchSequence = &copyOfSequence
		state.counts.Dispatched++
	case "deduplicated":
		if entry.State == "deduplicated" {
			return fail("duplicate_logical_dedup")
		}
		if !state.resumed || entry.State != "ambiguous" {
			return fail("origin_deduplication_not_ambiguous")
		}
		entry.State = "deduplicated"
		copyOfSequence := sequence
		entry.DedupSequence = &copyOfSequence
		state.counts.Deduplicated++
	default:
		return fail("invalid_corpus")
	}
	return nil
}

func onlyPayloadFields(payload payloadShape, allowed ...string) bool {
	present := map[string]bool{"type": payload.Type != ""}
	if payload.ActiveDurationMS != nil {
		present["active_duration_ms"] = true
	}
	if payload.Artifact != nil {
		present["artifact"] = true
	}
	if payload.ArtifactCount != nil {
		present["artifact_count"] = true
	}
	if payload.Code != nil {
		present["code"] = true
	}
	if payload.Disposition != nil {
		present["disposition"] = true
	}
	if payload.EligibleForCommit != nil {
		present["eligible_for_commit"] = true
	}
	if payload.FrameCount != nil {
		present["frame_count"] = true
	}
	if payload.MonitorBatches != nil {
		present["monitor_batches"] = true
	}
	if payload.Operation != nil {
		present["operation"] = true
	}
	if payload.OriginOperationCount != nil {
		present["origin_operation_count"] = true
	}
	if payload.OutputItems != nil {
		present["output_items"] = true
	}
	if payload.RequestFingerprint != nil {
		present["request_fingerprint"] = true
	}
	if payload.Status != nil {
		present["status"] = true
	}
	expected := map[string]bool{"type": true}
	for _, name := range allowed {
		expected[name] = true
	}
	return reflect.DeepEqual(present, expected)
}

func (state *validatorState) validateTerminal(payload payloadShape) error {
	if !onlyPayloadFields(
		payload,
		"active_duration_ms",
		"artifact_count",
		"eligible_for_commit",
		"frame_count",
		"monitor_batches",
		"origin_operation_count",
		"output_items",
		"status",
	) {
		return fail("invalid_corpus")
	}
	if payload.Status == nil || (*payload.Status != "success" && *payload.Status != "error" && *payload.Status != "cancelled") {
		return fail("invalid_corpus")
	}
	if *payload.FrameCount != state.counts.Frames || *payload.OutputItems != state.counts.Outputs || *payload.MonitorBatches != state.counts.MonitorBatches || *payload.ArtifactCount != state.counts.Artifacts || *payload.OriginOperationCount != uint64(len(state.ledger)) {
		return fail("terminal_count_mismatch")
	}
	if *payload.ActiveDurationMS > state.limits.MaxActiveDurationMS {
		return fail("active_duration_limit_exceeded")
	}
	unresolved := false
	for _, entry := range state.ledger {
		if entry.State == "ambiguous" {
			unresolved = true
		}
	}
	kindComplete := (state.request.Kind == "monitor" && state.counts.MonitorBatches > 0) || ((state.request.Kind == "scrape" || state.request.Kind == "browser") && state.counts.Outputs == 1)
	shouldCommit := *payload.Status == "success" && state.counts.Errors == 0 && !unresolved && kindComplete
	if *payload.EligibleForCommit != shouldCommit || (*payload.Status == "success" && !shouldCommit) || (*payload.Status == "error" && state.counts.Errors == 0) || ((*payload.Status == "cancelled") != state.cancelled) {
		return fail("terminal_count_mismatch")
	}
	state.terminal = &terminalResult{
		ActiveDurationMS:     *payload.ActiveDurationMS,
		ArtifactCount:        *payload.ArtifactCount,
		EligibleForCommit:    *payload.EligibleForCommit,
		FrameCount:           *payload.FrameCount,
		MonitorBatches:       *payload.MonitorBatches,
		OriginOperationCount: *payload.OriginOperationCount,
		OutputItems:          *payload.OutputItems,
		Status:               *payload.Status,
	}
	return nil
}

func (state *validatorState) applyPayload(payload payloadShape, sequence uint64, measurements measurementsShape) error {
	switch payload.Type {
	case "origin_operation_declared":
		if !onlyPayloadFields(payload, "operation") || payload.Operation == nil {
			return fail("invalid_corpus")
		}
		return state.addDynamicOperation(*payload.Operation)
	case "origin_contact":
		if !onlyPayloadFields(payload, "disposition", "operation", "request_fingerprint") {
			return fail("invalid_corpus")
		}
		return state.originContact(payload, sequence)
	case "monitor_batch":
		if !onlyPayloadFields(payload) || measurements.OutputItems == nil {
			return fail("invalid_corpus")
		}
		if state.request.Kind != "monitor" {
			return fail("wrong_frame_kind")
		}
		state.counts.MonitorBatches++
		state.counts.Outputs += *measurements.OutputItems
		copyOfSequence := sequence
		state.lastResultSequence = &copyOfSequence
		if state.counts.MonitorBatches > state.limits.MaxMonitorBatches {
			return fail("output_limit_exceeded")
		}
	case "scrape_result":
		if !onlyPayloadFields(payload) || state.request.Kind != "scrape" || state.lastResultSequence != nil {
			return fail("wrong_frame_kind")
		}
		state.counts.Outputs++
		copyOfSequence := sequence
		state.lastResultSequence = &copyOfSequence
	case "browser_result":
		if !onlyPayloadFields(payload) || state.request.Kind != "browser" || state.lastResultSequence != nil {
			return fail("wrong_frame_kind")
		}
		state.counts.Outputs++
		copyOfSequence := sequence
		state.lastResultSequence = &copyOfSequence
	case "artifact":
		if !onlyPayloadFields(payload, "artifact") || payload.Artifact == nil || payload.Artifact.Handle == "" {
			return fail("invalid_corpus")
		}
		if state.artifactHandles[payload.Artifact.Handle] {
			return fail("artifact_identity_reused")
		}
		state.artifactHandles[payload.Artifact.Handle] = true
		state.counts.Artifacts++
		state.counts.ArtifactBytes += payload.Artifact.SizeBytes
		if state.counts.Artifacts > state.limits.MaxArtifactCount {
			return fail("artifact_count_limit_exceeded")
		}
		if state.counts.ArtifactBytes > state.limits.MaxArtifactTotalBytes {
			return fail("artifact_total_bytes_limit_exceeded")
		}
	case "error":
		if !onlyPayloadFields(payload, "code") || payload.Code == nil || !runtimeErrorCodes[*payload.Code] {
			return fail("invalid_corpus")
		}
		state.counts.Errors++
		if state.counts.Errors > localMaxErrors {
			return fail("error_local_cap_exceeded")
		}
	case "terminal":
		return state.validateTerminal(payload)
	default:
		return fail("invalid_corpus")
	}
	if state.counts.Outputs > state.limits.MaxOutputItems {
		return fail("output_limit_exceeded")
	}
	return nil
}

func (state *validatorState) handleServer(event controlEvent) error {
	if event.ServerHello != nil {
		if event.Frame != nil || event.Measurements != nil || event.Fault != nil || event.Resume != nil || event.WindowUpdate != nil || event.Cancel != nil || event.ClientHello != nil || event.Start != nil {
			return fail("invalid_corpus")
		}
		return state.handleServerHello(*event.ServerHello)
	}
	if state.transportInvalidated {
		return fail("transport_invalidated")
	}
	if !state.hasRequest || event.Frame == nil || event.Measurements == nil || event.Fault != nil || event.Resume != nil || event.WindowUpdate != nil || event.Cancel != nil || event.ClientHello != nil || event.Start != nil {
		return fail("invalid_corpus")
	}
	frame := *event.Frame
	measurements := *event.Measurements
	if frame.ContractVersion != contractVersion || frame.RequestID != state.request.RequestID || frame.AttemptID != state.currentAttempt {
		return fail("binding_changed")
	}
	if frame.FenceDigest != state.request.FencingContext.FenceDigest {
		return fail("stale_fence")
	}
	if measurements.WireSizeBytes == 0 || measurements.WireSizeBytes > state.limits.MaxFrameBytes {
		return fail("frame_size_limit_exceeded")
	}
	if state.credit == 0 {
		return fail("credit_exceeded")
	}
	if frame.Payload.Type == "monitor_batch" {
		if measurements.OutputItems == nil {
			return fail("invalid_corpus")
		}
	} else if measurements.OutputItems != nil {
		return fail("invalid_corpus")
	}
	if state.cancelled && frame.Payload.Type != "terminal" {
		return fail("cancelled")
	}
	signature, err := frameSignature(frame, measurements)
	if err != nil {
		return fail("invalid_corpus")
	}
	if state.replayCursor != nil {
		if frame.Sequence < *state.replayCursor {
			return fail("sequence_rewind")
		}
		if frame.Sequence > *state.replayCursor {
			return fail("sequence_gap")
		}
		if signature != state.history[frame.Sequence] {
			return fail("divergent_sequence_reuse")
		}
		state.credit--
		state.counts.ReplayedFrames++
		state.lastPhysicalSequence = int64(frame.Sequence)
		state.lastPhysicalPayloadType = frame.Payload.Type
		if frame.Sequence == state.replayTo {
			state.replayCursor = nil
		} else {
			next := frame.Sequence + 1
			state.replayCursor = &next
		}
		return nil
	}
	if state.terminal != nil {
		if frame.Payload.Type == "terminal" {
			return fail("terminal_duplicate")
		}
		return fail("frame_after_terminal")
	}
	expected := state.lastSequence + 1
	if int64(frame.Sequence) < expected {
		return fail("sequence_rewind")
	}
	if int64(frame.Sequence) > expected {
		return fail("sequence_gap")
	}
	if frame.Payload.Type != "terminal" && state.counts.Frames+1 > state.limits.MaxExecutionFrames {
		return fail("frame_limit_exceeded")
	}
	state.credit--
	if err := state.applyPayload(frame.Payload, frame.Sequence, measurements); err != nil {
		return err
	}
	if frame.Payload.Type != "terminal" {
		state.counts.Frames++
	}
	state.lastSequence = int64(frame.Sequence)
	state.lastPayloadType = frame.Payload.Type
	state.lastPhysicalSequence = int64(frame.Sequence)
	state.lastPhysicalPayloadType = frame.Payload.Type
	state.history[frame.Sequence] = signature
	return nil
}

func (state *validatorState) run(events []controlEvent) (controlResult, error) {
	for eventIndex, event := range events {
		var err error
		if state.hasRequest && uint64(eventIndex) > state.durableCut && event.Direction != "fault" && state.deadlineExpired() {
			return state.result(false, "deadline_exceeded"), nil
		}
		switch event.Direction {
		case "client":
			err = state.handleClient(event)
		case "server":
			err = state.handleServer(event)
		case "fault":
			err = state.handleFault(event)
		default:
			err = fail("invalid_corpus")
		}
		if err != nil {
			return state.result(false, failureCode(err)), nil
		}
	}
	if state.replayCursor != nil {
		return state.result(false, "sequence_gap"), nil
	}
	if state.terminal == nil {
		return state.result(false, "terminal_missing"), nil
	}
	return state.result(true, "ok"), nil
}

func validateCaseMetadata(item controlCase) error {
	cut := item.Metadata.DurableCutEventIndex
	if len(item.Events) == 0 || cut >= uint64(len(item.Events)) || item.Events[cut].Direction == "fault" {
		return fail("invalid_corpus")
	}
	firstFault := -1
	for index, event := range item.Events {
		if event.Direction == "fault" {
			firstFault = index
			break
		}
	}
	if firstFault >= 0 {
		if cut+1 != uint64(firstFault) {
			return fail("fixture_cut_mismatch")
		}
		fault := item.Events[firstFault].Fault
		if fault == nil || item.Metadata.InjectionPhase != fault.Point {
			return fail("fixture_injection_phase_mismatch")
		}
		return nil
	}
	switch item.Metadata.InjectionPhase {
	case "none":
		if cut != 2 || item.Events[cut].Start == nil {
			return fail("fixture_cut_mismatch")
		}
	case "cancel":
		if cut+1 >= uint64(len(item.Events)) || item.Events[cut+1].Cancel == nil {
			return fail("fixture_injection_phase_mismatch")
		}
	case "deadline":
		if cut+1 >= uint64(len(item.Events)) {
			return fail("fixture_cut_mismatch")
		}
	default:
		return fail("fixture_injection_phase_mismatch")
	}
	return nil
}

func validateControlCase(item controlCase) controlResult {
	if err := validateCaseMetadata(item); err != nil {
		return emptyFailure(item.ID, failureCode(err))
	}
	state, err := newValidator(item)
	if err != nil {
		return emptyFailure(item.ID, failureCode(err))
	}
	result, err := state.run(item.Events)
	if err != nil {
		return state.result(false, failureCode(err))
	}
	return result
}

func loadControlCorpus(root string) (controlCorpus, error) {
	path := filepath.Join(root, "fixtures", "control", "manifest.json")
	content, err := os.ReadFile(path)
	if err != nil {
		return controlCorpus{}, err
	}
	var generic any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&generic); err != nil {
		return controlCorpus{}, err
	}
	pretty, err := json.MarshalIndent(generic, "", "  ")
	if err != nil {
		return controlCorpus{}, err
	}
	pretty = append(pretty, '\n')
	if !bytes.Equal(pretty, content) {
		return controlCorpus{}, fail("invalid_corpus")
	}
	var corpus controlCorpus
	if err := decodeStrict(content, &corpus); err != nil {
		return controlCorpus{}, fail("invalid_corpus")
	}
	if corpus.Format != controlFormat || len(corpus.Cases) == 0 {
		return controlCorpus{}, fail("invalid_corpus")
	}
	required := make([]string, 0, len(mandatoryControlCaseIDs))
	for caseID := range mandatoryControlCaseIDs {
		required = append(required, caseID)
	}
	sort.Strings(required)
	if !reflect.DeepEqual(corpus.RequiredCaseIDs, required) {
		return controlCorpus{}, fail("invalid_corpus")
	}
	seen := map[string]bool{}
	lastID := ""
	for _, item := range corpus.Cases {
		if item.ID == "" || seen[item.ID] || (lastID != "" && item.ID <= lastID) || !controlErrorCodes[item.Expected.Code] || item.Metadata.InjectionPhase == "" {
			return controlCorpus{}, fail("invalid_corpus")
		}
		if _, err := parseRFC3339(item.Metadata.LogicalTime); err != nil {
			return controlCorpus{}, err
		}
		if len(item.Events) == 0 || item.Metadata.DurableCutEventIndex >= uint64(len(item.Events)) || item.Events[item.Metadata.DurableCutEventIndex].Direction == "fault" {
			return controlCorpus{}, fail("invalid_corpus")
		}
		seen[item.ID] = true
		lastID = item.ID
	}
	for caseID := range mandatoryControlCaseIDs {
		if !seen[caseID] {
			return controlCorpus{}, fail("invalid_corpus")
		}
	}
	digestContent, err := os.ReadFile(filepath.Join(root, "fixtures", "control", "manifest.sha256"))
	if err != nil {
		return controlCorpus{}, err
	}
	digest := sha256.Sum256(content)
	expectedDigest := fmt.Sprintf("%x  manifest.json\n", digest)
	if string(digestContent) != expectedDigest {
		return controlCorpus{}, fail("invalid_corpus")
	}
	return corpus, nil
}

func validateControlCorpus(root string) ([]controlResult, error) {
	corpus, err := loadControlCorpus(root)
	if err != nil {
		return nil, err
	}
	results := make([]controlResult, 0, len(corpus.Cases))
	for _, item := range corpus.Cases {
		result := validateControlCase(item)
		if result.Accepted != item.Expected.Accepted || result.Code != item.Expected.Code {
			return nil, fmt.Errorf(
				"%s: expected %t/%s, got %t/%s",
				item.ID,
				item.Expected.Accepted,
				item.Expected.Code,
				result.Accepted,
				result.Code,
			)
		}
		results = append(results, result)
	}
	return results, nil
}
