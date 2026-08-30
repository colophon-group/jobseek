package chromiumservice

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

const chromiumServiceCorpusFormat = "jobseek.chromium-service-boundary/v1"

var chromiumServiceRequiredCaseIDs = []string{
	"accept_navigation_success",
	"accept_interaction_success",
	"accept_identity_success",
	"accept_caller_mutation_isolated",
	"accept_repeated_fresh_sessions",
	"unsupported_exact_preflight",
	"reject_null_assignment",
	"reject_lightpanda_backend",
	"reject_lightpanda_lane",
	"reject_unspecified_backend",
	"reject_unknown_backend",
	"reject_null_capability_class",
	"reject_capability_class_mismatch",
	"reject_null_service_lane",
	"reject_unknown_service_lane",
	"reject_empty_routing_revision",
	"reject_oversized_routing_revision",
	"reject_null_plan",
	"reject_empty_plan_capabilities",
	"reject_duplicate_plan_capability",
	"reject_unknown_plan_capability",
	"reject_wrong_contract_version",
	"reject_empty_target",
	"reject_origin_operation_limit",
	"reject_request_limit",
	"reject_unknown_provider_capability",
	"reject_duplicate_provider_capability",
	"reject_assignment_fingerprint_mismatch",
	"reject_error_partial_output",
	"reject_empty_provider_outcome",
	"reject_invalid_failure_pair",
	"error_open_timeout",
	"error_timeout",
	"error_cancelled",
	"error_target_lost",
	"error_session_lost",
	"error_process_crash",
	"error_protocol_failure",
	"error_resource_limit",
	"error_navigation",
	"error_cleanup_failure",
	"reject_process_down",
	"reject_pin_mismatch",
	"reject_process_unhealthy",
	"reject_process_age",
	"reject_rss_limit",
	"reject_session_leak",
	"reject_target_leak",
	"reject_pid_limit",
	"reject_file_descriptor_limit",
	"reject_socket_limit",
	"reject_pre_cancelled_context",
	"reject_after_shutdown",
	"reject_concurrency_saturation",
	"recycle_task_limit_between_tasks",
	"reject_config_unknown_field",
	"reject_config_mutable_image_tag",
	"reject_config_public_endpoint",
	"reject_config_zero_limit",
	"reject_config_root_uid",
	"reject_config_writable_root",
	"reject_config_new_privileges",
	"reject_config_retained_capabilities",
	"reject_config_seccomp",
	"reject_config_unbounded_tmpfs",
	"reject_config_secret_field",
	"reject_config_null_digest",
	"reject_open_error_with_session",
	"recycle_post_task_rss_limit",
}

type corpusCase struct {
	Expected map[string]any `json:"expected"`
	ID       string         `json:"id"`
	Input    map[string]any `json:"input"`
}

type corpusManifest struct {
	Cases            []corpusCase   `json:"cases"`
	Defaults         map[string]any `json:"defaults"`
	ExpectedDefaults map[string]any `json:"expected_defaults"`
	Format           string         `json:"format"`
	RequiredCaseIDs  []string       `json:"required_case_ids"`
}

type corpusDecision struct {
	CloseCalls    int           `json:"close_calls"`
	Code          string        `json:"code"`
	ExecuteCalls  int           `json:"execute_calls"`
	HealthReason  HealthReason  `json:"health_reason"`
	OpenCalls     int           `json:"open_calls"`
	OriginCalls   int           `json:"origin_calls"`
	Outcome       string        `json:"outcome"`
	Ready         bool          `json:"ready"`
	RecycleCalls  int           `json:"recycle_calls"`
	RecycleReason RecycleReason `json:"recycle_reason"`
}

func loadChromiumServiceCorpus(t *testing.T) corpusManifest {
	t.Helper()
	root := filepath.Join("..", "fixtures", "chromium_service")
	content, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var document any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&document); err != nil {
		t.Fatal(err)
	}
	canonical, err := json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	canonical = append(canonical, '\n')
	if !bytes.Equal(content, canonical) {
		t.Fatal("chromium service manifest is not canonical compact JSON")
	}
	digest := sha256.Sum256(content)
	expectedDigest, err := os.ReadFile(filepath.Join(root, "manifest.sha256"))
	if err != nil {
		t.Fatal(err)
	}
	actualDigest := hex.EncodeToString(digest[:]) + "  manifest.json\n"
	if string(expectedDigest) != actualDigest {
		t.Fatalf("manifest digest = %q, want %q", expectedDigest, actualDigest)
	}
	var manifest corpusManifest
	if err := json.Unmarshal(content, &manifest); err != nil {
		t.Fatal(err)
	}
	return manifest
}

func TestChromiumServiceCorpusRegistryAndDecisions(t *testing.T) {
	manifest := loadChromiumServiceCorpus(t)
	if manifest.Format != chromiumServiceCorpusFormat {
		t.Fatalf("format = %q", manifest.Format)
	}
	if !reflect.DeepEqual(manifest.RequiredCaseIDs, chromiumServiceRequiredCaseIDs) {
		t.Fatalf("required case IDs differ\n got: %v\nwant: %v", manifest.RequiredCaseIDs, chromiumServiceRequiredCaseIDs)
	}
	if len(manifest.Cases) != len(chromiumServiceRequiredCaseIDs) {
		t.Fatalf("case count = %d, want %d", len(manifest.Cases), len(chromiumServiceRequiredCaseIDs))
	}
	seen := make(map[string]bool, len(manifest.Cases))
	for index, item := range manifest.Cases {
		if item.ID != chromiumServiceRequiredCaseIDs[index] || seen[item.ID] {
			t.Fatalf("invalid case ID/order at %d: %q", index, item.ID)
		}
		seen[item.ID] = true
		item := item
		t.Run(item.ID, func(t *testing.T) {
			merged, ok := deepMergeCorpus(manifest.Defaults, item.Input).(map[string]any)
			if !ok {
				t.Fatal("merged input is not an object")
			}
			expectedValue := deepMergeCorpus(manifest.ExpectedDefaults, item.Expected)
			expectedJSON, err := json.Marshal(expectedValue)
			if err != nil {
				t.Fatal(err)
			}
			var expected corpusDecision
			if err := json.Unmarshal(expectedJSON, &expected); err != nil {
				t.Fatal(err)
			}
			if actual := runCorpusCase(t, merged); actual != expected {
				t.Fatalf("decision = %+v, want %+v", actual, expected)
			}
		})
	}
}

func runCorpusCase(t *testing.T, input map[string]any) corpusDecision {
	t.Helper()
	configValue, _ := input["config"].(map[string]any)
	configJSON, err := json.Marshal(configValue)
	if err != nil {
		t.Fatal(err)
	}
	config, err := DecodeConfig(bytes.NewReader(configJSON))
	if err != nil {
		var configFailure *ConfigError
		if !errors.As(err, &configFailure) {
			t.Fatal(err)
		}
		return corpusDecision{
			Code:          string(configFailure.Code),
			HealthReason:  "config_error",
			Outcome:       "config_error",
			RecycleReason: RecycleNone,
		}
	}

	providerValue, _ := input["provider"].(map[string]any)
	provider := providerFromCorpus(providerValue, config)
	service, err := New(config, provider)
	if err != nil {
		t.Fatal(err)
	}
	runtimeInput := runtimeInputFromCorpus(input)
	mode := corpusString(input, "mode")
	var result *runtimev1.BrowserResult
	switch mode {
	case "repeat":
		_ = service.Execute(context.Background(), runtimeInput)
		result = service.Execute(context.Background(), runtimeInputFromCorpus(input))
	case "caller_mutation":
		provider.mutateOriginal = runtimeInput
		result = service.Execute(context.Background(), runtimeInput)
	case "shutdown":
		if err := service.Shutdown(context.Background()); err != nil {
			t.Fatal(err)
		}
		result = service.Execute(context.Background(), runtimeInput)
	case "saturated":
		provider.entered = make(chan struct{})
		provider.release = make(chan struct{})
		firstDone := make(chan *runtimev1.BrowserResult, 1)
		go func() {
			firstDone <- service.Execute(context.Background(), runtimeInput)
		}()
		<-provider.entered
		result = service.Execute(context.Background(), runtimeInputFromCorpus(input))
		close(provider.release)
		first := <-firstDone
		if resultCode(first) != "success" {
			t.Fatalf("first saturated task = %v", first)
		}
	default:
		ctx := context.Background()
		if corpusBool(input, "context_cancelled") {
			cancelled, cancel := context.WithCancel(ctx)
			cancel()
			ctx = cancelled
		}
		result = service.Execute(ctx, runtimeInput)
	}

	health := service.Health()
	provider.mu.Lock()
	decision := corpusDecision{
		CloseCalls:    provider.closeCalls,
		Code:          resultCode(result),
		ExecuteCalls:  provider.executeCalls,
		HealthReason:  health.Reason,
		OpenCalls:     provider.openCalls,
		OriginCalls:   provider.origins,
		Outcome:       resultOutcome(result),
		Ready:         health.Ready,
		RecycleCalls:  len(provider.recycleCalls),
		RecycleReason: health.RecycleReason,
	}
	provider.mu.Unlock()
	if decision.ExecuteCalls > decision.OpenCalls || decision.OpenCalls > 2 {
		t.Fatalf("invalid provider call conservation: %+v", decision)
	}
	if mode != "repeat" && decision.ExecuteCalls > 1 {
		t.Fatalf("semantic retry observed: %+v", decision)
	}
	return decision
}

func providerFromCorpus(value map[string]any, config Config) *fakeProvider {
	provider := newFakeProvider()
	provider.capabilities = corpusCapabilities(value["capabilities"])
	provider.openOutcome = corpusString(value, "open")
	provider.executeOutcome = corpusString(value, "execute")
	provider.cleanupOK = corpusString(value, "cleanup") == "ok"
	provider.fingerprintOK = corpusString(value, "fingerprint") == "match"
	provider.originCalls = corpusInt(value, "origin_calls")
	provider.before = corpusSnapshot(corpusString(value, "snapshot_before"), config)
	provider.after = corpusSnapshot(corpusString(value, "snapshot_after"), config)
	return provider
}

func runtimeInputFromCorpus(input map[string]any) *runtimev1.BrowserExecutionInput {
	if !corpusBool(input, "plan_present") && input["assignment"] == nil {
		return &runtimev1.BrowserExecutionInput{}
	}
	var plan *runtimev1.BrowserPlan
	if corpusBool(input, "plan_present") {
		plan = &runtimev1.BrowserPlan{
			ContractVersion:      corpusString(input, "plan_contract_version"),
			TargetUrl:            corpusString(input, "target_url"),
			RequiredCapabilities: corpusCapabilities(input["plan_capabilities"]),
		}
		for index := 0; index < corpusInt(input, "origin_operations"); index++ {
			plan.OriginOperations = append(plan.OriginOperations, &runtimev1.OriginOperationRef{
				OriginRequestId: "origin-" + strings.Repeat("x", index%3+1),
			})
		}
		for index := 0; index < corpusInt(input, "plan_operations"); index++ {
			plan.Actions = append(plan.Actions, &runtimev1.BrowserAction{})
		}
	}
	assignmentValue, assignmentPresent := input["assignment"]
	var assignment *runtimev1.BrowserAssignment
	if object, ok := assignmentValue.(map[string]any); ok && assignmentPresent {
		assignment = &runtimev1.BrowserAssignment{
			Backend:         corpusBackend(object["backend"]),
			CapabilityClass: corpusCapabilityClass(object["capability_class"]),
			ServiceLane:     corpusServiceLane(object["service_lane"]),
			RoutingRevision: corpusString(object, "routing_revision"),
		}
	}
	return &runtimev1.BrowserExecutionInput{Plan: plan, Assignment: assignment}
}

func corpusSnapshot(name string, config Config) ProcessSnapshot {
	snapshot := ProcessSnapshot{Live: true, Healthy: true, PinsExact: true}
	switch name {
	case "down":
		snapshot.Live = false
	case "pins_mismatch":
		snapshot.PinsExact = false
	case "unhealthy":
		snapshot.Healthy = false
	case "age":
		snapshot.AgeMS = config.MaxProcessAgeMS
	case "rss":
		snapshot.RSSBytes = config.MaxRSSBytes + 1
	case "session_leak":
		snapshot.OpenSessions = 1
	case "target_leak":
		snapshot.OpenTargets = 1
	case "pids":
		snapshot.PIDs = config.MaxPIDs + 1
	case "file_descriptors":
		snapshot.FileDescriptors = config.MaxFileDescriptors + 1
	case "sockets":
		snapshot.Sockets = config.MaxSockets + 1
	}
	return snapshot
}

func corpusCapabilities(value any) []runtimev1.BrowserCapability {
	items, _ := value.([]any)
	capabilities := make([]runtimev1.BrowserCapability, 0, len(items))
	for _, item := range items {
		name, _ := item.(string)
		switch name {
		case "render":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER)
		case "evaluate":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE)
		case "actions":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS)
		case "pagination":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PAGINATION)
		case "response_capture":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE)
		case "request_interception":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_REQUEST_INTERCEPTION)
		case "frames":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES)
		case "persistent_session":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION)
		case "headful_identity":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_HEADFUL_IDENTITY)
		case "proxy":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY)
		case "transport_overrides":
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES)
		default:
			capabilities = append(capabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_UNSPECIFIED)
		}
	}
	return capabilities
}

func corpusBackend(value any) runtimev1.BrowserBackend {
	switch value {
	case "chromium":
		return runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
	case "lightpanda":
		return runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA
	default:
		return runtimev1.BrowserBackend_BROWSER_BACKEND_UNSPECIFIED
	}
}

func corpusCapabilityClass(value any) runtimev1.BrowserCapabilityClass {
	switch value {
	case "navigation_evaluation":
		return runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION
	case "interaction_capture":
		return runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_INTERACTION_CAPTURE
	case "identity_transport":
		return runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_IDENTITY_TRANSPORT
	default:
		return runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_UNSPECIFIED
	}
}

func corpusServiceLane(value any) runtimev1.BrowserServiceLane {
	switch value {
	case "chromium":
		return runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_CHROMIUM
	case "lightpanda":
		return runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_LIGHTPANDA
	default:
		return runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_UNSPECIFIED
	}
}

func resultOutcome(result *runtimev1.BrowserResult) string {
	switch {
	case result.GetSuccess() != nil:
		return "success"
	case result.GetUnsupported() != nil:
		return "unsupported"
	default:
		return "error"
	}
}

func deepMergeCorpus(base any, override any) any {
	baseMap, baseOK := base.(map[string]any)
	overrideMap, overrideOK := override.(map[string]any)
	if !baseOK || !overrideOK {
		return cloneCorpusValue(override)
	}
	merged := make(map[string]any, len(baseMap)+len(overrideMap))
	for key, value := range baseMap {
		merged[key] = cloneCorpusValue(value)
	}
	for key, value := range overrideMap {
		if current, exists := merged[key]; exists {
			merged[key] = deepMergeCorpus(current, value)
		} else {
			merged[key] = cloneCorpusValue(value)
		}
	}
	return merged
}

func cloneCorpusValue(value any) any {
	payload, _ := json.Marshal(value)
	var cloned any
	_ = json.Unmarshal(payload, &cloned)
	return cloned
}

func corpusString(object map[string]any, key string) string {
	value, _ := object[key].(string)
	return value
}

func corpusBool(object map[string]any, key string) bool {
	value, _ := object[key].(bool)
	return value
}

func corpusInt(object map[string]any, key string) int {
	switch value := object[key].(type) {
	case float64:
		return int(value)
	case json.Number:
		parsed, _ := value.Int64()
		return int(parsed)
	case int:
		return value
	default:
		return 0
	}
}

func TestConfigSchemaHasClosedSecuritySurface(t *testing.T) {
	content, err := os.ReadFile("config.schema.json")
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]any
	if err := json.Unmarshal(content, &schema); err != nil {
		t.Fatal(err)
	}
	if schema["additionalProperties"] != false {
		t.Fatal("config schema must reject unknown fields")
	}
	properties, _ := schema["properties"].(map[string]any)
	required, _ := schema["required"].([]any)
	if len(properties) != 28 || len(required) != len(properties) {
		t.Fatalf("properties/required = %d/%d", len(properties), len(required))
	}
	for _, forbidden := range []string{
		"credential", "environment", "redis", "database", "typesense", "r2", "proxy_url", "cdp_credential",
	} {
		if bytes.Contains(bytes.ToLower(content), []byte(forbidden)) {
			t.Fatalf("forbidden config surface %q", forbidden)
		}
	}
}
