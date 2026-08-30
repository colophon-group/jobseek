package chromiumservice

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

type fakeProvider struct {
	mu sync.Mutex

	capabilities    []runtimev1.BrowserCapability
	before          ProcessSnapshot
	after           ProcessSnapshot
	useAfter        bool
	openOutcome     string
	executeOutcome  string
	cleanupOK       bool
	fingerprintOK   bool
	originCalls     int
	mutateOriginal  *runtimev1.BrowserExecutionInput
	entered         chan struct{}
	release         chan struct{}
	ignoreCancel    bool
	snapshotEntered chan int
	snapshotRelease chan struct{}
	snapshotBlocks  int

	openCalls         int
	executeCalls      int
	closeCalls        int
	origins           int
	sessionSequence   int
	isolationFailures int
	recycleCalls      []RecycleReason
	tasks             []BoundTask
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
	snapshot := provider.before
	if provider.useAfter {
		snapshot = provider.after
	}
	entered := provider.snapshotEntered
	release := provider.snapshotRelease
	sequence := 0
	if provider.snapshotBlocks > 0 {
		sequence = provider.snapshotBlocks
		provider.snapshotBlocks--
	}
	provider.mu.Unlock()
	if sequence != 0 {
		entered <- sequence
		<-release
	}
	return snapshot
}

func (provider *fakeProvider) OpenSession(
	_ context.Context,
	_ SessionLimits,
) (ChromiumSession, *ProviderFailure) {
	provider.mu.Lock()
	defer provider.mu.Unlock()
	provider.openCalls++
	provider.sessionSequence++
	session := &fakeSession{provider: provider, id: provider.sessionSequence}
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
	id       int
	cookie   bool
	storage  bool
	target   bool
	context  bool
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
	ignoreCancel := provider.ignoreCancel
	outcomeName := provider.executeOutcome
	fingerprintOK := provider.fingerprintOK
	provider.mu.Unlock()

	if entered != nil {
		close(entered)
	}
	if release != nil {
		if ignoreCancel {
			<-release
		} else {
			select {
			case <-release:
			case <-ctx.Done():
				outcomeName = "cancelled"
			}
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
	case "artifact_limit":
		outcome.Success = &runtimev1.BrowserSuccess{
			FinalUrl: "https://example.test/final",
			Artifacts: []*runtimev1.ArtifactHandle{{
				Handle: "artifact-oversized", SizeBytes: 16*1024*1024 + 1,
			}},
		}
	case "manifest_limit":
		size := uint64(16*1024*1024 + 1)
		outcome.Success = &runtimev1.BrowserSuccess{
			FinalUrl: "https://example.test/final",
			Html: &runtimev1.ChunkManifest{
				Complete: true, TotalSizeBytes: size,
				Chunks: []*runtimev1.DataChunk{{
					SizeBytes: size,
					Storage: &runtimev1.DataChunk_Artifact{Artifact: &runtimev1.ArtifactHandle{
						Handle: "manifest-oversized", SizeBytes: size,
					}},
				}},
			},
		}
	case "transfer_overflow":
		outcome.Success = &runtimev1.BrowserSuccess{
			FinalUrl: "https://example.test/final",
			Html: &runtimev1.ChunkManifest{
				Complete: true,
				Chunks: []*runtimev1.DataChunk{
					{
						SizeBytes: ^uint64(0),
						Storage: &runtimev1.DataChunk_Artifact{Artifact: &runtimev1.ArtifactHandle{
							Handle: "overflow-a", SizeBytes: ^uint64(0),
						}},
					},
					{
						Sequence: 1, SizeBytes: 1,
						Storage: &runtimev1.DataChunk_Artifact{Artifact: &runtimev1.ArtifactHandle{
							Handle: "overflow-b", SizeBytes: 1,
						}},
					},
				},
			},
		}
	case "stateful_isolation":
		assignment := task.Assignment()
		provider.mu.Lock()
		if session.id < 1 || session.cookie || session.storage || session.target ||
			session.context || assignment == nil ||
			assignment.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM {
			provider.isolationFailures++
		}
		session.cookie = true
		session.storage = true
		session.target = true
		session.context = true
		provider.mu.Unlock()
		if assignment != nil {
			assignment.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA
		}
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

func TestShutdownCancelsActiveTaskAndClosesWithinOneGrace(t *testing.T) {
	for _, cleanupOK := range []bool{true, false} {
		cleanupOK := cleanupOK
		t.Run(map[bool]string{true: "cleanup succeeds", false: "cleanup overrides cancellation"}[cleanupOK], func(t *testing.T) {
			provider := newFakeProvider()
			provider.cleanupOK = cleanupOK
			provider.entered = make(chan struct{})
			provider.release = make(chan struct{})
			service, err := New(validConfig(), provider)
			if err != nil {
				t.Fatal(err)
			}
			result := make(chan *runtimev1.BrowserResult, 1)
			go func() { result <- service.Execute(context.Background(), validInput()) }()
			<-provider.entered
			if err := service.Shutdown(context.Background()); err != nil {
				t.Fatal(err)
			}
			got := <-result
			wantCode := "cancelled"
			if !cleanupOK {
				wantCode = "internal"
			}
			if resultCode(got) != wantCode {
				t.Fatalf("result = %v, want %s", got, wantCode)
			}
			provider.mu.Lock()
			defer provider.mu.Unlock()
			if provider.openCalls != 1 || provider.executeCalls != 1 || provider.closeCalls != 1 {
				t.Fatalf("open/execute/close = %d/%d/%d", provider.openCalls, provider.executeCalls, provider.closeCalls)
			}
		})
	}
}

func TestShutdownGraceCannotBeExtendedByLaterCall(t *testing.T) {
	provider := newFakeProvider()
	provider.entered = make(chan struct{})
	provider.release = make(chan struct{})
	provider.ignoreCancel = true
	config := validConfig()
	config.ShutdownGraceMS = 20
	service, err := New(config, provider)
	if err != nil {
		t.Fatal(err)
	}
	result := make(chan *runtimev1.BrowserResult, 1)
	go func() { result <- service.Execute(context.Background(), validInput()) }()
	<-provider.entered
	if err := service.Shutdown(context.Background()); !errors.Is(err, ErrShutdownTimeout) {
		t.Fatalf("first shutdown = %v", err)
	}
	service.mu.Lock()
	firstDeadline := service.shutdownDeadline
	service.mu.Unlock()
	if err := service.Shutdown(context.Background()); !errors.Is(err, ErrShutdownTimeout) {
		t.Fatalf("second shutdown = %v", err)
	}
	service.mu.Lock()
	secondDeadline := service.shutdownDeadline
	service.mu.Unlock()
	if !secondDeadline.Equal(firstDeadline) {
		t.Fatalf("shutdown deadline changed: %s -> %s", firstDeadline, secondDeadline)
	}
	close(provider.release)
	select {
	case got := <-result:
		if resultCode(got) != "internal" {
			t.Fatalf("late cleanup result = %v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("active task did not finish after provider release")
	}
	for range 100 {
		if err := service.Shutdown(context.Background()); err != nil {
			t.Fatalf("idle shutdown after expired grace = %v", err)
		}
	}
	service.mu.Lock()
	finalDeadline := service.shutdownDeadline
	service.mu.Unlock()
	if !finalDeadline.Equal(firstDeadline) {
		t.Fatalf("shutdown deadline changed after idle: %s -> %s", firstDeadline, finalDeadline)
	}
}

func TestRepeatedIdleShutdownIsDeterministicAfterDeadline(t *testing.T) {
	config := validConfig()
	config.ShutdownGraceMS = 2
	service, err := New(config, newFakeProvider())
	if err != nil {
		t.Fatal(err)
	}
	if err := service.Shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	service.mu.Lock()
	deadline := service.shutdownDeadline
	service.mu.Unlock()
	if remaining := time.Until(deadline); remaining > 0 {
		time.Sleep(remaining + time.Millisecond)
	}

	const repetitions = 256
	errors := make(chan error, repetitions)
	var callers sync.WaitGroup
	callers.Add(repetitions)
	for index := range repetitions {
		go func() {
			defer callers.Done()
			ctx := context.Background()
			if index%2 == 0 {
				cancelled, cancel := context.WithCancel(ctx)
				cancel()
				ctx = cancelled
			}
			errors <- service.Shutdown(ctx)
		}()
	}
	callers.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatalf("repeated idle shutdown = %v", err)
		}
	}
	service.mu.Lock()
	finalDeadline := service.shutdownDeadline
	service.mu.Unlock()
	if !finalDeadline.Equal(deadline) {
		t.Fatalf("shutdown deadline changed: %s -> %s", deadline, finalDeadline)
	}
}

func TestPostCloseLeakDiscardsSuccessAndRecyclesImmediatelyOnce(t *testing.T) {
	for _, test := range []struct {
		name     string
		snapshot ProcessSnapshot
		reason   RecycleReason
	}{
		{name: "session", snapshot: ProcessSnapshot{Live: true, Healthy: true, PinsExact: true, OpenSessions: 1}, reason: RecycleSessionLeak},
		{name: "target", snapshot: ProcessSnapshot{Live: true, Healthy: true, PinsExact: true, OpenTargets: 1}, reason: RecycleTargetLeak},
	} {
		t.Run(test.name, func(t *testing.T) {
			provider := newFakeProvider()
			provider.after = test.snapshot
			service, err := New(validConfig(), provider)
			if err != nil {
				t.Fatal(err)
			}
			if result := service.Execute(context.Background(), validInput()); resultCode(result) != "internal" || result.GetSuccess() != nil {
				t.Fatalf("leaked success = %v", result)
			}
			_ = service.Health()
			provider.mu.Lock()
			defer provider.mu.Unlock()
			if len(provider.recycleCalls) != 1 || provider.recycleCalls[0] != test.reason {
				t.Fatalf("recycles = %v, want one %s", provider.recycleCalls, test.reason)
			}
		})
	}
}

func TestIdleHealthRefreshesCurrentSnapshot(t *testing.T) {
	for _, test := range []struct {
		name     string
		snapshot ProcessSnapshot
		health   HealthReason
		recycle  RecycleReason
	}{
		{name: "process crash", snapshot: ProcessSnapshot{PinsExact: true}, health: HealthProcessDown, recycle: RecycleProcessCrash},
		{name: "pin mismatch", snapshot: ProcessSnapshot{Live: true, Healthy: true}, health: HealthPinsMismatch, recycle: RecycleNone},
		{name: "resource limit", snapshot: ProcessSnapshot{Live: true, Healthy: true, PinsExact: true, PIDs: validConfig().MaxPIDs + 1}, health: HealthResourceLimit, recycle: RecycleFailedHealth},
	} {
		t.Run(test.name, func(t *testing.T) {
			provider := newFakeProvider()
			service, err := New(validConfig(), provider)
			if err != nil {
				t.Fatal(err)
			}
			provider.mu.Lock()
			provider.after = test.snapshot
			provider.useAfter = true
			provider.mu.Unlock()
			health := service.Health()
			if health.Ready || health.Reason != test.health || health.RecycleReason != test.recycle {
				t.Fatalf("health = %+v", health)
			}
			provider.mu.Lock()
			defer provider.mu.Unlock()
			wantCalls := 0
			if test.recycle != RecycleNone {
				wantCalls = 1
			}
			if len(provider.recycleCalls) != wantCalls {
				t.Fatalf("recycle calls = %v, want %d", provider.recycleCalls, wantCalls)
			}
		})
	}
}

func TestHealthFailsClosedWhenBothSnapshotCommitsRaceLifecycle(t *testing.T) {
	provider := newFakeProvider()
	service, err := New(validConfig(), provider)
	if err != nil {
		t.Fatal(err)
	}
	provider.mu.Lock()
	provider.snapshotEntered = make(chan int, 2)
	provider.snapshotRelease = make(chan struct{}, 2)
	provider.snapshotBlocks = 2
	provider.mu.Unlock()
	done := make(chan Health, 1)
	go func() { done <- service.Health() }()
	for range 2 {
		<-provider.snapshotEntered
		service.mu.Lock()
		service.lifecycleVersion++
		service.mu.Unlock()
		provider.snapshotRelease <- struct{}{}
	}
	select {
	case health := <-done:
		if health.Ready || health.Reason != HealthInitializing {
			t.Fatalf("racing health = %+v", health)
		}
	case <-time.After(time.Second):
		t.Fatal("health did not complete after bounded retries")
	}
}

func TestDeclaredSuccessTransferBudgetIsOverflowSafeAndDeduplicated(t *testing.T) {
	handle := func(id string, size uint64) *runtimev1.ArtifactHandle {
		return &runtimev1.ArtifactHandle{Handle: id, MediaType: "text/html", SizeBytes: size, Sha256: "digest"}
	}
	shared := handle("shared", 8)
	if !declaredSuccessBytesWithinLimit(&runtimev1.BrowserSuccess{
		Html: &runtimev1.ChunkManifest{
			Complete: true, TotalSizeBytes: 8,
			Chunks: []*runtimev1.DataChunk{{
				SizeBytes: 8,
				Storage:   &runtimev1.DataChunk_Artifact{Artifact: shared},
			}},
		},
		Artifacts: []*runtimev1.ArtifactHandle{proto.Clone(shared).(*runtimev1.ArtifactHandle)},
	}, 8) {
		t.Fatal("identical artifact handle was not deduplicated at exact bound")
	}
	if declaredSuccessBytesWithinLimit(&runtimev1.BrowserSuccess{
		Artifacts: []*runtimev1.ArtifactHandle{handle("a", 8), handle("b", 1)},
	}, 8) {
		t.Fatal("accepted declared artifact bytes above limit")
	}
	if declaredSuccessBytesWithinLimit(&runtimev1.BrowserSuccess{
		Artifacts: []*runtimev1.ArtifactHandle{handle("shared", 8), handle("shared", 7)},
	}, 8) {
		t.Fatal("accepted inconsistent duplicate artifact identity")
	}
	if declaredSuccessBytesWithinLimit(&runtimev1.BrowserSuccess{
		Html: &runtimev1.ChunkManifest{Complete: false},
	}, 8) {
		t.Fatal("accepted incomplete manifest")
	}
	if declaredSuccessBytesWithinLimit(&runtimev1.BrowserSuccess{
		Html: &runtimev1.ChunkManifest{
			Complete: true,
			Chunks: []*runtimev1.DataChunk{
				{SizeBytes: ^uint64(0), Storage: &runtimev1.DataChunk_Artifact{Artifact: handle("a", ^uint64(0))}},
				{Sequence: 1, SizeBytes: 1, Storage: &runtimev1.DataChunk_Artifact{Artifact: handle("b", 1)}},
			},
		},
	}, ^uint64(0)) {
		t.Fatal("accepted overflowing manifest total")
	}
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
