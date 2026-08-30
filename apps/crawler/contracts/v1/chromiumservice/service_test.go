package chromiumservice

import (
	"context"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

type fakeProvider struct {
	mu sync.Mutex

	capabilities   []runtimev1.BrowserCapability
	before         ProcessSnapshot
	after          ProcessSnapshot
	useAfter       bool
	openOutcome    string
	executeOutcome string
	cleanupOK      bool
	fingerprintOK  bool
	originCalls    int
	mutateOriginal *runtimev1.BrowserExecutionInput
	entered        chan struct{}
	release        chan struct{}

	openCalls    int
	executeCalls int
	closeCalls   int
	origins      int
	recycleCalls []RecycleReason
	tasks        []BoundTask
}

func newFakeProvider() *fakeProvider {
	ready := ProcessSnapshot{Live: true, Healthy: true, PinsExact: true}
	return &fakeProvider{
		capabilities: []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PAGINATION,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_REQUEST_INTERCEPTION,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_HEADFUL_IDENTITY,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES,
		},
		before:         ready,
		after:          ready,
		openOutcome:    "ok",
		executeOutcome: "success",
		cleanupOK:      true,
		fingerprintOK:  true,
		originCalls:    1,
	}
}

func (provider *fakeProvider) Capabilities() []runtimev1.BrowserCapability {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	return append([]runtimev1.BrowserCapability(nil), provider.capabilities...)
}

func (provider *fakeProvider) Snapshot() ProcessSnapshot {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	if provider.useAfter {
		return provider.after
	}
	return provider.before
}

func (provider *fakeProvider) OpenSession(
	_ context.Context,
	_ SessionLimits,
) (ChromiumSession, *ProviderFailure) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.openCalls++
	session := &fakeSession{provider: provider}
	switch provider.openOutcome {
	case "ok":
		return session, nil
	case "nil":
		provider.useAfter = true
		return nil, nil
	case "timeout":
		provider.useAfter = true
		return nil, providerFailure("timeout")
	case "error_with_session":
		return session, providerFailure("timeout")
	default:
		provider.useAfter = true
		return nil, &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		}
	}
}

func (provider *fakeProvider) RequestRecycle(reason RecycleReason) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.recycleCalls = append(provider.recycleCalls, reason)
}

type fakeSession struct {
	provider *fakeProvider
}

func (session *fakeSession) Execute(ctx context.Context, task BoundTask) ProviderOutcome {
	provider := session.provider
	provider.mu.Lock()
	provider.executeCalls++
	provider.origins += provider.originCalls
	provider.tasks = append(provider.tasks, task)
	if provider.mutateOriginal != nil && provider.mutateOriginal.Assignment != nil {
		provider.mutateOriginal.Assignment.Backend =
			runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA
	}
	entered := provider.entered
	release := provider.release
	outcomeName := provider.executeOutcome
	fingerprintOK := provider.fingerprintOK
	provider.mu.Unlock()

	if entered != nil {
		close(entered)
	}
	if release != nil {
		select {
		case <-release:
		case <-ctx.Done():
			outcomeName = "cancelled"
		}
	}

	fingerprint := task.Fingerprint()
	if !fingerprintOK {
		fingerprint[0] ^= 0xff
	}
	outcome := ProviderOutcome{BindingFingerprint: fingerprint}
	switch outcomeName {
	case "success":
		outcome.Success = &runtimev1.BrowserSuccess{FinalUrl: "https://example.test/final"}
	case "partial_timeout":
		outcome.Success = &runtimev1.BrowserSuccess{FinalUrl: "https://sensitive.test/partial"}
		outcome.Failure = providerFailure("timeout")
	case "empty":
	case "invalid_pair":
		outcome.Failure = &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		}
	default:
		outcome.Failure = providerFailure(outcomeName)
	}
	return outcome
}

func (session *fakeSession) Close(_ context.Context) *ProviderFailure {
	provider := session.provider
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.closeCalls++
	provider.useAfter = true
	if !provider.cleanupOK {
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			Recycle:     RecycleCleanupFailure,
		}
	}
	return nil
}

func providerFailure(name string) *ProviderFailure {
	switch name {
	case "timeout":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_TIMEOUT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		}
	case "cancelled":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
		}
	case "target_lost":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_TARGET_LOST,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
			Recycle:     RecycleProtocolFailure,
		}
	case "session_lost":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_SESSION_LOST,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
			Recycle:     RecycleSessionLeak,
		}
	case "crash":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_TRANSPORT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
			Recycle:     RecycleProcessCrash,
		}
	case "protocol":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			Recycle:     RecycleProtocolFailure,
		}
	case "resource":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		}
	case "navigation":
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_NAVIGATION,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY,
		}
	default:
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		}
	}
}

func validConfig() Config {
	return Config{
		Backend:              BackendChromium,
		ServiceLane:          LaneChromium,
		ImageDigest:          "sha256:" + strings.Repeat("1", 64),
		BrowserDigest:        "sha256:" + strings.Repeat("2", 64),
		CDPClientVersion:     "cdp-1.0",
		BrowserVersion:       "chromium-140.0",
		SocketPath:           "/run/jobseek/chromium/control.sock",
		EgressPolicyRevision: "egress-1",
		MaxConcurrency:       1,
		ActiveSessionTTLMS:   10_000,
		ShutdownGraceMS:      1_000,
		MaxProcessAgeMS:      60_000,
		MaxRSSBytes:          512 * 1024 * 1024,
		MaxTargets:           1,
		MaxPIDs:              64,
		MaxFileDescriptors:   1024,
		MaxSockets:           128,
		MaxOriginOperations:  16,
		MaxRequests:          32,
		MaxTransferBytes:     16 * 1024 * 1024,
		RecycleAfterTasks:    100,
		RunAsUID:             10001,
		RunAsGID:             10001,
		ReadOnlyRoot:         true,
		NoNewPrivileges:      true,
		DropAllCapabilities:  true,
		SeccompProfile:       requiredSeccompProfile,
		WritableTmpfsBytes:   64 * 1024 * 1024,
	}
}

func validInput(capabilities ...runtimev1.BrowserCapability) *runtimev1.BrowserExecutionInput {
	if len(capabilities) == 0 {
		capabilities = []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
		}
	}
	return &runtimev1.BrowserExecutionInput{
		Plan: &runtimev1.BrowserPlan{
			ContractVersion:      runtimeContractVersionV1,
			TargetUrl:            "https://example.test/jobs",
			RequiredCapabilities: capabilities,
			OriginOperations: []*runtimev1.OriginOperationRef{
				{OriginRequestId: "origin-1"},
			},
		},
		Assignment: &runtimev1.BrowserAssignment{
			Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM,
			CapabilityClass: derivedCapabilityClass(capabilities),
			ServiceLane:     runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_CHROMIUM,
			RoutingRevision: "route-1",
		},
	}
}

func resultCode(result *runtimev1.BrowserResult) string {
	switch {
	case result == nil:
		return "nil"
	case result.GetSuccess() != nil:
		return "success"
	case result.GetUnsupported() != nil:
		return "unsupported_capability"
	case result.GetError() == nil || result.GetError().Error == nil:
		return "invalid"
	default:
		return strings.ToLower(strings.TrimPrefix(
			result.GetError().Error.Code.String(),
			"ERROR_CODE_",
		))
	}
}

func TestExecuteBindsCopyAndConstructsChromiumResult(t *testing.T) {
	provider := newFakeProvider()
	service, err := New(validConfig(), provider)
	if err != nil {
		t.Fatal(err)
	}
	input := validInput()
	provider.mutateOriginal = input
	result := service.Execute(context.Background(), input)
	if resultCode(result) != "success" ||
		result.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM {
		t.Fatalf("result = %v", result)
	}
	if input.Assignment.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA {
		t.Fatal("fake did not mutate caller-owned assignment")
	}
	provider.mu.Lock()
	defer provider.mu.Unlock()
	if len(provider.tasks) != 1 ||
		provider.tasks[0].Assignment().Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM {
		t.Fatal("bound assignment was influenced by caller mutation")
	}
	if provider.openCalls != 1 || provider.executeCalls != 1 || provider.closeCalls != 1 {
		t.Fatalf("open/execute/close = %d/%d/%d", provider.openCalls, provider.executeCalls, provider.closeCalls)
	}
}

func TestUnsupportedIsExactAndPreSession(t *testing.T) {
	provider := newFakeProvider()
	provider.capabilities = []runtimev1.BrowserCapability{
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
	}
	service, err := New(validConfig(), provider)
	if err != nil {
		t.Fatal(err)
	}
	result := service.Execute(context.Background(), validInput(
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
	))
	want := []runtimev1.BrowserCapability{
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
		runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
	}
	if got := result.GetUnsupported().Capabilities; !equalCapabilities(got, want) {
		t.Fatalf("missing = %v, want %v", got, want)
	}
	provider.mu.Lock()
	defer provider.mu.Unlock()
	if provider.openCalls != 0 || provider.executeCalls != 0 || provider.origins != 0 {
		t.Fatalf("unsupported performed work: %+v", provider)
	}
}

func TestFailureDiscardsAuthoritativePartialOutput(t *testing.T) {
	provider := newFakeProvider()
	provider.executeOutcome = "partial_timeout"
	service, err := New(validConfig(), provider)
	if err != nil {
		t.Fatal(err)
	}
	result := service.Execute(context.Background(), validInput())
	if resultCode(result) != "internal" || result.GetSuccess() != nil ||
		result.GetError().Error.Message == "https://sensitive.test/partial" {
		t.Fatalf("partial output escaped: %v", result)
	}
}

func TestActiveSessionTTLAndOutputBytesAreEnforced(t *testing.T) {
	t.Run("active session TTL", func(t *testing.T) {
		provider := newFakeProvider()
		provider.release = make(chan struct{})
		config := validConfig()
		config.ActiveSessionTTLMS = 5
		service, err := New(config, provider)
		if err != nil {
			t.Fatal(err)
		}
		result := service.Execute(context.Background(), validInput())
		if resultCode(result) != "timeout" {
			t.Fatalf("result = %v", result)
		}
		provider.mu.Lock()
		defer provider.mu.Unlock()
		if provider.executeCalls != 1 || provider.closeCalls != 1 {
			t.Fatalf("execute/close = %d/%d", provider.executeCalls, provider.closeCalls)
		}
	})

	t.Run("provider output bytes", func(t *testing.T) {
		provider := newFakeProvider()
		config := validConfig()
		config.MaxTransferBytes = 1
		service, err := New(config, provider)
		if err != nil {
			t.Fatal(err)
		}
		result := service.Execute(context.Background(), validInput())
		if resultCode(result) != "resource_limit" || result.GetSuccess() != nil {
			t.Fatalf("result = %v", result)
		}
		health := service.Health()
		if health.RecycleReason != RecycleProtocolFailure || health.Ready {
			t.Fatalf("health = %+v", health)
		}
	})
}

func TestDecodeConfigStrictRoundTrip(t *testing.T) {
	payload, err := json.Marshal(validConfig())
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodeConfig(strings.NewReader(string(payload)))
	if err != nil {
		t.Fatal(err)
	}
	if decoded != validConfig() {
		t.Fatalf("decoded = %+v", decoded)
	}
	for _, suffix := range []string{
		` {}`,
		``,
	} {
		candidate := string(payload) + suffix
		if suffix == "" {
			candidate = strings.Replace(candidate, `"backend":"chromium"`, `"backend":null`, 1)
		}
		if _, err := DecodeConfig(strings.NewReader(candidate)); err == nil {
			t.Fatalf("accepted invalid config %q", suffix)
		}
	}
}

func equalCapabilities(
	left []runtimev1.BrowserCapability,
	right []runtimev1.BrowserCapability,
) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
