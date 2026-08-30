package adjacentpolicy

// Candidate-only BrowserExecutor assignment conformance. This file has no
// runtime, provider, origin, queue, persistence, or network authority.

import (
	"bytes"
	"encoding/json"
	"io"
	"regexp"
	"sort"
)

const browserBoundaryFormat = "jobseek.browser-executor-boundary/v1"

var browserRoutingRevisionPattern = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`)

type browserBackend uint8

const (
	browserBackendUnspecified browserBackend = iota
	browserBackendChromium
	browserBackendLightpanda
)

type browserCapabilityClass uint8

const (
	browserCapabilityClassUnspecified browserCapabilityClass = iota
	browserCapabilityClassNavigationEvaluation
	browserCapabilityClassInteractionCapture
	browserCapabilityClassIdentityTransport
)

type browserServiceLane uint8

const (
	browserServiceLaneUnspecified browserServiceLane = iota
	browserServiceLaneLightpanda
	browserServiceLaneChromium
)

type browserCapability uint8

const (
	browserCapabilityUnspecified browserCapability = iota
	browserCapabilityRender
	browserCapabilityEvaluate
	browserCapabilityActions
	browserCapabilityPagination
	browserCapabilityResponseCapture
	browserCapabilityRequestInterception
	browserCapabilityFrames
	browserCapabilityPersistentSession
	browserCapabilityHeadfulIdentity
	browserCapabilityProxy
	browserCapabilityTransportOverrides
)

type browserAssignment struct {
	Backend         browserBackend
	CapabilityClass browserCapabilityClass
	ServiceLane     browserServiceLane
	RoutingRevision string
}

type browserResult struct {
	Backend                 browserBackend
	Outcome                 string
	ErrorCode               *string
	UnsupportedCapabilities []browserCapability
}

var browserBackendByName = map[string]browserBackend{
	"unspecified": browserBackendUnspecified,
	"chromium":    browserBackendChromium,
	"lightpanda":  browserBackendLightpanda,
}

var browserCapabilityClassByName = map[string]browserCapabilityClass{
	"unspecified":           browserCapabilityClassUnspecified,
	"navigation_evaluation": browserCapabilityClassNavigationEvaluation,
	"interaction_capture":   browserCapabilityClassInteractionCapture,
	"identity_transport":    browserCapabilityClassIdentityTransport,
}

var browserServiceLaneByName = map[string]browserServiceLane{
	"unspecified": browserServiceLaneUnspecified,
	"chromium":    browserServiceLaneChromium,
	"lightpanda":  browserServiceLaneLightpanda,
}

var browserCapabilityByName = map[string]browserCapability{
	"unspecified":          browserCapabilityUnspecified,
	"render":               browserCapabilityRender,
	"evaluate":             browserCapabilityEvaluate,
	"actions":              browserCapabilityActions,
	"pagination":           browserCapabilityPagination,
	"response_capture":     browserCapabilityResponseCapture,
	"request_interception": browserCapabilityRequestInterception,
	"frames":               browserCapabilityFrames,
	"persistent_session":   browserCapabilityPersistentSession,
	"headful_identity":     browserCapabilityHeadfulIdentity,
	"proxy":                browserCapabilityProxy,
	"transport_overrides":  browserCapabilityTransportOverrides,
}

var browserErrorCodeByName = map[string]bool{
	"tdm_reserved":     true,
	"provider_gone":    true,
	"permanent_gone":   true,
	"http_status":      true,
	"timeout":          true,
	"transport":        true,
	"anti_bot":         true,
	"invalid_config":   true,
	"empty_result":     true,
	"internal":         true,
	"target_lost":      true,
	"session_lost":     true,
	"resource_limit":   true,
	"cancelled":        true,
	"ambiguous_origin": true,
	"navigation":       true,
}

var browserInputKeys = []string{
	"assignment",
	"assignment_after_bind",
	"origin_before_assignment",
	"origin_operations",
	"plan_capabilities",
	"provider_capabilities",
	"provider_invocations",
	"result",
}

var browserAssignmentKeys = []string{
	"backend",
	"capability_class",
	"routing_revision",
	"service_lane",
}

var browserResultKeys = []string{
	"backend",
	"error_code",
	"outcome",
	"partial_output_present",
	"unsupported_capabilities",
}

type browserBoundaryDecision struct {
	Code   string `json:"code"`
	Status string `json:"status"`
}

type browserAssignmentJSON struct {
	Backend         string `json:"backend"`
	CapabilityClass string `json:"capability_class"`
	RoutingRevision string `json:"routing_revision"`
	ServiceLane     string `json:"service_lane"`
}

type browserResultJSON struct {
	Backend                 string   `json:"backend"`
	ErrorCode               *string  `json:"error_code"`
	Outcome                 string   `json:"outcome"`
	PartialOutputPresent    bool     `json:"partial_output_present"`
	UnsupportedCapabilities []string `json:"unsupported_capabilities"`
}

type browserBoundaryInputJSON struct {
	Assignment             *browserAssignmentJSON `json:"assignment"`
	AssignmentAfterBind    *browserAssignmentJSON `json:"assignment_after_bind"`
	OriginBeforeAssignment bool                   `json:"origin_before_assignment"`
	OriginOperations       int                    `json:"origin_operations"`
	PlanCapabilities       []string               `json:"plan_capabilities"`
	ProviderCapabilities   []string               `json:"provider_capabilities"`
	ProviderInvocations    []string               `json:"provider_invocations"`
	Result                 browserResultJSON      `json:"result"`
}

func browserDecision(status string, code string) browserBoundaryDecision {
	return browserBoundaryDecision{Status: status, Code: code}
}

func browserExactKeys(value any, expected []string) bool {
	object, ok := value.(map[string]any)
	if !ok || len(object) != len(expected) {
		return false
	}
	for _, key := range expected {
		if _, exists := object[key]; !exists {
			return false
		}
	}
	return true
}

func browserDecodeStrict(value map[string]any, output any) bool {
	content, err := json.Marshal(value)
	if err != nil {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(output); err != nil {
		return false
	}
	return decoder.Decode(&struct{}{}) == io.EOF
}

func browserCloneJSON(value any) any {
	switch typed := value.(type) {
	case map[string]any:
		cloned := make(map[string]any, len(typed))
		for key, item := range typed {
			cloned[key] = browserCloneJSON(item)
		}
		return cloned
	case []any:
		cloned := make([]any, len(typed))
		for index, item := range typed {
			cloned[index] = browserCloneJSON(item)
		}
		return cloned
	default:
		return value
	}
}

func browserDeepMerge(base any, override any) any {
	baseObject, baseOK := base.(map[string]any)
	overrideObject, overrideOK := override.(map[string]any)
	if !baseOK || !overrideOK {
		return browserCloneJSON(override)
	}
	merged := browserCloneJSON(baseObject).(map[string]any)
	for key, value := range overrideObject {
		if existing, exists := merged[key]; exists {
			merged[key] = browserDeepMerge(existing, value)
		} else {
			merged[key] = browserCloneJSON(value)
		}
	}
	return merged
}

func browserParseCapabilities(
	values []string,
	allowEmpty bool,
) ([]browserCapability, bool) {
	if !allowEmpty && len(values) == 0 {
		return nil, false
	}
	parsed := make([]browserCapability, 0, len(values))
	seen := make(map[browserCapability]bool, len(values))
	for _, name := range values {
		capability, exists := browserCapabilityByName[name]
		if !exists || capability == browserCapabilityUnspecified || seen[capability] {
			return nil, false
		}
		seen[capability] = true
		parsed = append(parsed, capability)
	}
	return parsed, true
}

func browserDerivedCapabilityClass(
	capabilities []browserCapability,
) browserCapabilityClass {
	class := browserCapabilityClassNavigationEvaluation
	for _, capability := range capabilities {
		switch capability {
		case browserCapabilityFrames,
			browserCapabilityPersistentSession,
			browserCapabilityHeadfulIdentity,
			browserCapabilityProxy,
			browserCapabilityTransportOverrides:
			return browserCapabilityClassIdentityTransport
		case browserCapabilityActions,
			browserCapabilityPagination,
			browserCapabilityResponseCapture,
			browserCapabilityRequestInterception:
			class = browserCapabilityClassInteractionCapture
		}
	}
	return class
}

func browserParseAssignment(
	value any,
) (*browserAssignment, bool) {
	if !browserExactKeys(value, browserAssignmentKeys) {
		return nil, false
	}
	object := value.(map[string]any)
	var raw browserAssignmentJSON
	if !browserDecodeStrict(object, &raw) {
		return nil, false
	}
	backend, backendOK := browserBackendByName[raw.Backend]
	class, classOK := browserCapabilityClassByName[raw.CapabilityClass]
	lane, laneOK := browserServiceLaneByName[raw.ServiceLane]
	if !backendOK || !classOK || !laneOK ||
		backend == browserBackendUnspecified ||
		class == browserCapabilityClassUnspecified ||
		lane == browserServiceLaneUnspecified {
		return nil, false
	}
	return &browserAssignment{
		Backend:         backend,
		CapabilityClass: class,
		ServiceLane:     lane,
		RoutingRevision: raw.RoutingRevision,
	}, true
}

func browserExpectedLane(backend browserBackend) browserServiceLane {
	if backend == browserBackendLightpanda {
		return browserServiceLaneLightpanda
	}
	return browserServiceLaneChromium
}

func browserMissingCapabilities(
	required []browserCapability,
	provided []browserCapability,
) []browserCapability {
	available := make(map[browserCapability]bool, len(provided))
	for _, capability := range provided {
		available[capability] = true
	}
	missing := make([]browserCapability, 0)
	for _, capability := range required {
		if !available[capability] {
			missing = append(missing, capability)
		}
	}
	sort.Slice(missing, func(left int, right int) bool { return missing[left] < missing[right] })
	return missing
}

func browserSameCapabilities(left []browserCapability, right []browserCapability) bool {
	if len(left) != len(right) {
		return false
	}
	leftCopy := append([]browserCapability(nil), left...)
	rightCopy := append([]browserCapability(nil), right...)
	sort.Slice(leftCopy, func(first int, second int) bool { return leftCopy[first] < leftCopy[second] })
	sort.Slice(rightCopy, func(first int, second int) bool { return rightCopy[first] < rightCopy[second] })
	for index := range leftCopy {
		if leftCopy[index] != rightCopy[index] {
			return false
		}
	}
	return true
}

func browserBuildResult(raw browserResultJSON) (*browserResult, bool) {
	backend, exists := browserBackendByName[raw.Backend]
	if !exists || backend == browserBackendUnspecified {
		return nil, false
	}
	result := &browserResult{Backend: backend, Outcome: raw.Outcome, ErrorCode: raw.ErrorCode}
	switch raw.Outcome {
	case "success":
	case "error":
		if raw.ErrorCode == nil {
			return nil, false
		}
		if !browserErrorCodeByName[*raw.ErrorCode] {
			return nil, false
		}
	case "unsupported":
		capabilities, ok := browserParseCapabilities(raw.UnsupportedCapabilities, true)
		if !ok {
			return nil, false
		}
		result.UnsupportedCapabilities = capabilities
	default:
		return nil, false
	}
	return result, true
}

func evaluateBrowserBoundary(inputValue map[string]any) browserBoundaryDecision {
	if !browserExactKeys(inputValue, browserInputKeys) ||
		!browserExactKeys(inputValue["result"], browserResultKeys) {
		return browserDecision("rejected", "invalid_input")
	}
	var input browserBoundaryInputJSON
	if !browserDecodeStrict(inputValue, &input) || input.OriginOperations < 0 {
		return browserDecision("rejected", "invalid_input")
	}

	assignment, ok := browserParseAssignment(inputValue["assignment"])
	if !ok {
		return browserDecision("rejected", "invalid_assignment")
	}
	planCapabilities, ok := browserParseCapabilities(input.PlanCapabilities, false)
	if !ok {
		return browserDecision("rejected", "invalid_capabilities")
	}
	if assignment.CapabilityClass != browserDerivedCapabilityClass(planCapabilities) {
		return browserDecision("rejected", "capability_class_mismatch")
	}
	if assignment.ServiceLane != browserExpectedLane(assignment.Backend) {
		return browserDecision("rejected", "service_lane_mismatch")
	}
	if !browserRoutingRevisionPattern.MatchString(assignment.RoutingRevision) {
		return browserDecision("rejected", "routing_revision_invalid")
	}
	if input.AssignmentAfterBind != nil {
		afterValue := inputValue["assignment_after_bind"]
		after, valid := browserParseAssignment(afterValue)
		if !valid || *assignment != *after {
			return browserDecision("rejected", "assignment_changed")
		}
	}
	if input.OriginBeforeAssignment {
		return browserDecision("rejected", "origin_before_assignment")
	}

	providerCapabilities, ok := browserParseCapabilities(input.ProviderCapabilities, true)
	if !ok {
		return browserDecision("rejected", "invalid_provider_capabilities")
	}
	result, ok := browserBuildResult(input.Result)
	if !ok {
		if input.Result.Outcome == "error" {
			return browserDecision("rejected", "error_result_invalid")
		}
		return browserDecision("rejected", "invalid_result")
	}
	if result.Backend != assignment.Backend {
		return browserDecision("rejected", "backend_mismatch")
	}

	missing := browserMissingCapabilities(planCapabilities, providerCapabilities)
	if len(missing) > 0 {
		if len(input.ProviderInvocations) != 0 {
			return browserDecision("rejected", "unsupported_execution_forbidden")
		}
		if input.OriginOperations != 0 {
			return browserDecision("rejected", "unsupported_origin_forbidden")
		}
		if result.Outcome != "unsupported" || input.Result.ErrorCode != nil || input.Result.PartialOutputPresent ||
			!browserSameCapabilities(missing, result.UnsupportedCapabilities) {
			return browserDecision("rejected", "unsupported_result_invalid")
		}
		return browserDecision("unsupported", "unsupported_capability")
	}

	if len(input.ProviderInvocations) == 0 {
		return browserDecision("rejected", "provider_invocation_missing")
	}
	if len(input.ProviderInvocations) > 1 {
		for _, name := range input.ProviderInvocations {
			backend, exists := browserBackendByName[name]
			if !exists || backend != assignment.Backend {
				return browserDecision("rejected", "fallback_forbidden")
			}
		}
		return browserDecision("rejected", "retry_forbidden")
	}
	providerBackend, exists := browserBackendByName[input.ProviderInvocations[0]]
	if !exists || providerBackend != assignment.Backend {
		return browserDecision("rejected", "backend_mismatch")
	}

	switch input.Result.Outcome {
	case "success":
		if input.OriginOperations == 0 {
			return browserDecision("rejected", "origin_operation_missing")
		}
		if input.Result.ErrorCode != nil || len(input.Result.UnsupportedCapabilities) != 0 ||
			input.Result.PartialOutputPresent || result.Outcome != "success" {
			return browserDecision("rejected", "success_result_invalid")
		}
		return browserDecision("accepted", "success")
	case "error":
		if input.Result.ErrorCode == nil || len(input.Result.UnsupportedCapabilities) != 0 ||
			input.Result.PartialOutputPresent || result.Outcome != "error" {
			return browserDecision("rejected", "error_result_invalid")
		}
		return browserDecision("accepted", "provider_error")
	case "unsupported":
		return browserDecision("rejected", "unexpected_unsupported")
	default:
		return browserDecision("rejected", "invalid_result")
	}
}
