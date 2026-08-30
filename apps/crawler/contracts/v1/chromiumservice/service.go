package chromiumservice

import (
	"context"
	"crypto/sha256"
	"errors"
	"regexp"
	"sort"
	"sync"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

var routingRevisionPattern = regexp.MustCompile(`^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$`)

var ErrShutdownTimeout = errors.New("chromium service shutdown grace expired")

// SessionLimits is the bounded subset a dormant provider adapter may receive.
// It contains no endpoint, credential, environment, or deployment authority.
type SessionLimits struct {
	ActiveSessionTTLMS  uint64
	MaxOriginOperations uint32
	MaxRequests         uint32
	MaxTransferBytes    uint64
}

// ChromiumProvider combines the future process supervisor and provider
// boundary so one task cannot discover or select a second backend.
type ChromiumProvider interface {
	Capabilities() []runtimev1.BrowserCapability
	Snapshot() ProcessSnapshot
	OpenSession(context.Context, SessionLimits) (ChromiumSession, *ProviderFailure)
	RequestRecycle(RecycleReason)
}

// ChromiumSession is fresh per task. Execute is called at most once and Close
// is always called once after a successful OpenSession.
type ChromiumSession interface {
	Execute(context.Context, BoundTask) ProviderOutcome
	Close(context.Context) *ProviderFailure
}

// ProviderFailure is intentionally message-free. The service validates its
// closed code/disposition pair and emits its own bounded message.
type ProviderFailure struct {
	Code        runtimev1.ErrorCode
	Disposition runtimev1.ErrorDisposition
	Recycle     RecycleReason
}

// ProviderOutcome carries exactly one success or failure and echoes the bound
// fingerprint. A malformed outcome is discarded and fails closed.
type ProviderOutcome struct {
	BindingFingerprint [sha256.Size]byte
	Success            *runtimev1.BrowserSuccess
	Failure            *ProviderFailure
}

// BoundTask exposes copies only. A provider cannot mutate the service's bound
// plan or assignment after fingerprinting.
type BoundTask struct {
	plan        *runtimev1.BrowserPlan
	assignment  *runtimev1.BrowserAssignment
	fingerprint [sha256.Size]byte
}

func (task BoundTask) Plan() *runtimev1.BrowserPlan {
	if task.plan == nil {
		return nil
	}
	return proto.Clone(task.plan).(*runtimev1.BrowserPlan)
}

func (task BoundTask) Assignment() *runtimev1.BrowserAssignment {
	if task.assignment == nil {
		return nil
	}
	return proto.Clone(task.assignment).(*runtimev1.BrowserAssignment)
}

func (task BoundTask) Fingerprint() [sha256.Size]byte {
	return task.fingerprint
}

// Service is a dormant in-process state machine. It owns no listener, process,
// queue, goroutine, or production lifecycle.
type Service struct {
	config   Config
	provider ChromiumProvider

	mu               sync.Mutex
	snapshot         ProcessSnapshot
	active           uint32
	completedTasks   uint64
	lastCleanupOK    bool
	recycleReason    RecycleReason
	recycleRequested bool
	shuttingDown     bool
	idle             chan struct{}
}

func New(config Config, provider ChromiumProvider) (*Service, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	if provider == nil {
		return nil, configError(ConfigInvalidIdentity)
	}
	idle := make(chan struct{})
	close(idle)
	service := &Service{
		config:        config,
		provider:      provider,
		lastCleanupOK: true,
		recycleReason: RecycleNone,
		idle:          idle,
	}
	service.snapshot = provider.Snapshot()
	return service, nil
}

// Execute validates and binds before consulting provider capabilities or
// process state. It performs zero semantic retries and has no fallback path.
func (service *Service) Execute(
	ctx context.Context,
	input *runtimev1.BrowserExecutionInput,
) *runtimev1.BrowserResult {
	if service == nil {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if ctx == nil {
		ctx = context.Background()
	}
	bound, failure := service.bind(input)
	if failure != nil {
		return failureResult(failure.Code, failure.Disposition)
	}
	if ctx.Err() != nil {
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
		)
	}

	snapshot := service.provider.Snapshot()
	if failure, recycle, acquired := service.acquire(snapshot); !acquired {
		service.requestRecycleIfIdle(recycle)
		return failureResult(failure.Code, failure.Disposition)
	}

	capabilities, valid := normalizedCapabilities(service.provider.Capabilities(), true)
	if !valid {
		result := failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
		service.finish(service.provider.Snapshot(), RecycleProtocolFailure, true)
		return result
	}
	missing := missingCapabilities(bound.plan.RequiredCapabilities, capabilities)
	if len(missing) != 0 {
		result := unsupportedResult(missing)
		service.finish(service.provider.Snapshot(), RecycleNone, true)
		return result
	}

	session, openFailure := service.provider.OpenSession(ctx, SessionLimits{
		ActiveSessionTTLMS:  service.config.ActiveSessionTTLMS,
		MaxOriginOperations: service.config.MaxOriginOperations,
		MaxRequests:         service.config.MaxRequests,
		MaxTransferBytes:    service.config.MaxTransferBytes,
	})
	if openFailure != nil || session == nil {
		if session != nil {
			cleanupContext, cancelCleanup := context.WithTimeout(
				context.Background(),
				time.Duration(service.config.ShutdownGraceMS)*time.Millisecond,
			)
			_ = session.Close(cleanupContext)
			cancelCleanup()
			service.finish(service.provider.Snapshot(), RecycleProtocolFailure, false)
			return failureResult(
				runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
				runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
			)
		}
		validated, recycle := validatedFailure(openFailure)
		result := failureResult(validated.Code, validated.Disposition)
		service.finish(service.provider.Snapshot(), recycle, true)
		return result
	}

	executeContext, cancelExecute := context.WithTimeout(
		ctx,
		time.Duration(service.config.ActiveSessionTTLMS)*time.Millisecond,
	)
	outcome := session.Execute(executeContext, bound)
	executeContextError := executeContext.Err()
	cancelExecute()
	cleanupContext, cancelCleanup := context.WithTimeout(
		context.Background(),
		time.Duration(service.config.ShutdownGraceMS)*time.Millisecond,
	)
	cleanupFailure := session.Close(cleanupContext)
	cancelCleanup()
	postSnapshot := service.provider.Snapshot()

	if cleanupFailure != nil {
		service.finish(postSnapshot, RecycleCleanupFailure, false)
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if executeContextError != nil {
		service.finish(postSnapshot, RecycleNone, true)
		if errors.Is(executeContextError, context.DeadlineExceeded) {
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
	if outcome.BindingFingerprint != bound.fingerprint ||
		(outcome.Success == nil) == (outcome.Failure == nil) {
		service.finish(postSnapshot, RecycleProtocolFailure, true)
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		)
	}
	if outcome.Failure != nil {
		validated, recycle := validatedFailure(outcome.Failure)
		service.finish(postSnapshot, recycle, true)
		return failureResult(validated.Code, validated.Disposition)
	}
	if !service.successWithinLimits(bound.plan, outcome.Success) {
		service.finish(postSnapshot, RecycleProtocolFailure, true)
		return failureResult(
			runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		)
	}

	success := proto.Clone(outcome.Success).(*runtimev1.BrowserSuccess)
	service.finish(postSnapshot, RecycleNone, true)
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersionV1,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM,
		Outcome:         &runtimev1.BrowserResult_Success{Success: success},
	}
}

func (service *Service) successWithinLimits(
	plan *runtimev1.BrowserPlan,
	success *runtimev1.BrowserSuccess,
) bool {
	if plan == nil || success == nil || len(success.FinalUrl) > 8192 ||
		uint64(proto.Size(success)) > service.config.MaxTransferBytes ||
		len(success.ActionOutcomes) > len(plan.Actions) ||
		len(success.Captures) > len(plan.Captures) ||
		len(success.Evaluations) > len(plan.Evaluations) ||
		len(success.Artifacts) > int(service.config.MaxRequests) {
		return false
	}
	return true
}

// Shutdown rejects new work and waits only for sessions already owned by this
// service. It launches no goroutine and never kills a shared process.
func (service *Service) Shutdown(ctx context.Context) error {
	if service == nil {
		return nil
	}
	if ctx == nil {
		ctx = context.Background()
	}
	service.mu.Lock()
	service.shuttingDown = true
	idle := service.idle
	service.mu.Unlock()

	grace, cancel := context.WithTimeout(
		ctx,
		time.Duration(service.config.ShutdownGraceMS)*time.Millisecond,
	)
	defer cancel()
	select {
	case <-idle:
		return nil
	case <-grace.Done():
		return ErrShutdownTimeout
	}
}

func (service *Service) bind(input *runtimev1.BrowserExecutionInput) (BoundTask, *ProviderFailure) {
	invalid := &ProviderFailure{
		Code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
		Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
	}
	if input == nil || input.Plan == nil || input.Assignment == nil {
		return BoundTask{}, invalid
	}
	cloned := proto.Clone(input).(*runtimev1.BrowserExecutionInput)
	plan := cloned.Plan
	assignment := cloned.Assignment
	capabilities, valid := normalizedCapabilities(plan.RequiredCapabilities, false)
	if !valid || plan.ContractVersion != runtimeContractVersionV1 || plan.TargetUrl == "" ||
		len(plan.OriginOperations) > int(service.config.MaxOriginOperations) ||
		len(plan.OriginOperations) > int(service.config.MaxRequests) ||
		len(plan.Actions)+len(plan.Captures)+len(plan.Evaluations)+len(plan.Interceptions) > int(service.config.MaxRequests) ||
		assignment.Backend != runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM ||
		assignment.ServiceLane != runtimev1.BrowserServiceLane_BROWSER_SERVICE_LANE_CHROMIUM ||
		assignment.CapabilityClass != derivedCapabilityClass(capabilities) ||
		!routingRevisionPattern.MatchString(assignment.RoutingRevision) {
		return BoundTask{}, invalid
	}
	plan.RequiredCapabilities = capabilities
	encoded, err := proto.MarshalOptions{Deterministic: true}.Marshal(assignment)
	if err != nil {
		return BoundTask{}, invalid
	}
	return BoundTask{
		plan:        plan,
		assignment:  assignment,
		fingerprint: sha256.Sum256(encoded),
	}, nil
}

func (service *Service) acquire(snapshot ProcessSnapshot) (*ProviderFailure, RecycleReason, bool) {
	service.mu.Lock()
	defer service.mu.Unlock()
	service.snapshot = snapshot
	if service.shuttingDown {
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_CANCELLED,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_CANCELLED_POLICY,
		}, RecycleNone, false
	}
	if service.recycleReason != RecycleNone {
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		}, service.recycleReason, false
	}
	if !snapshot.PinsExact {
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_INVALID_CONFIG_POLICY,
		}, RecycleNone, false
	}
	recycle := service.snapshotRecycleReason(snapshot)
	if recycle != RecycleNone {
		code := runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT
		disposition := runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY
		if !snapshot.Live || !snapshot.Healthy {
			code = runtimev1.ErrorCode_ERROR_CODE_INTERNAL
			disposition = runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY
		}
		service.recycleReason = recycle
		return &ProviderFailure{Code: code, Disposition: disposition}, recycle, false
	}
	if service.active >= service.config.MaxConcurrency {
		return &ProviderFailure{
			Code:        runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
			Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY,
		}, RecycleNone, false
	}
	if service.active == 0 {
		service.idle = make(chan struct{})
	}
	service.active++
	return nil, RecycleNone, true
}

func (service *Service) finish(snapshot ProcessSnapshot, reason RecycleReason, cleanupOK bool) {
	service.mu.Lock()
	service.snapshot = snapshot
	service.lastCleanupOK = cleanupOK
	service.completedTasks++
	if reason == RecycleNone {
		reason = service.snapshotRecycleReason(snapshot)
	}
	if reason == RecycleNone && service.completedTasks >= service.config.RecycleAfterTasks {
		reason = RecycleTaskLimit
	}
	if service.recycleReason == RecycleNone && reason != RecycleNone {
		service.recycleReason = reason
	}
	if service.active > 0 {
		service.active--
	}
	idle := service.active == 0
	if idle {
		close(service.idle)
	}
	request := idle && service.recycleReason != RecycleNone && !service.recycleRequested
	requestedReason := service.recycleReason
	if request {
		service.recycleRequested = true
	}
	service.mu.Unlock()
	if request {
		service.provider.RequestRecycle(requestedReason)
	}
}

func (service *Service) requestRecycleIfIdle(reason RecycleReason) {
	if reason == RecycleNone {
		return
	}
	service.mu.Lock()
	if service.recycleReason == RecycleNone {
		service.recycleReason = reason
	}
	request := service.active == 0 && !service.recycleRequested
	requestedReason := service.recycleReason
	if request {
		service.recycleRequested = true
	}
	service.mu.Unlock()
	if request {
		service.provider.RequestRecycle(requestedReason)
	}
}

func normalizedCapabilities(
	values []runtimev1.BrowserCapability,
	allowEmpty bool,
) ([]runtimev1.BrowserCapability, bool) {
	if len(values) == 0 && !allowEmpty {
		return nil, false
	}
	seen := make(map[runtimev1.BrowserCapability]bool, len(values))
	normalized := append([]runtimev1.BrowserCapability(nil), values...)
	for _, capability := range normalized {
		if !knownCapability(capability) || seen[capability] {
			return nil, false
		}
		seen[capability] = true
	}
	sort.Slice(normalized, func(left int, right int) bool { return normalized[left] < normalized[right] })
	return normalized, true
}

func knownCapability(capability runtimev1.BrowserCapability) bool {
	return capability >= runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER &&
		capability <= runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES
}

func derivedCapabilityClass(
	capabilities []runtimev1.BrowserCapability,
) runtimev1.BrowserCapabilityClass {
	class := runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION
	for _, capability := range capabilities {
		switch capability {
		case runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PERSISTENT_SESSION,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_HEADFUL_IDENTITY,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_TRANSPORT_OVERRIDES:
			return runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_IDENTITY_TRANSPORT
		case runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PAGINATION,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RESPONSE_CAPTURE,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_REQUEST_INTERCEPTION:
			class = runtimev1.BrowserCapabilityClass_BROWSER_CAPABILITY_CLASS_INTERACTION_CAPTURE
		}
	}
	return class
}

func missingCapabilities(
	required []runtimev1.BrowserCapability,
	provided []runtimev1.BrowserCapability,
) []runtimev1.BrowserCapability {
	available := make(map[runtimev1.BrowserCapability]bool, len(provided))
	for _, capability := range provided {
		available[capability] = true
	}
	missing := make([]runtimev1.BrowserCapability, 0)
	for _, capability := range required {
		if !available[capability] {
			missing = append(missing, capability)
		}
	}
	sort.Slice(missing, func(left int, right int) bool { return missing[left] < missing[right] })
	return missing
}

func validatedFailure(failure *ProviderFailure) (*ProviderFailure, RecycleReason) {
	invalid := &ProviderFailure{
		Code:        runtimev1.ErrorCode_ERROR_CODE_INTERNAL,
		Disposition: runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
	}
	if failure == nil || !validFailurePair(failure.Code, failure.Disposition) {
		return invalid, RecycleProtocolFailure
	}
	recycle := failure.Recycle
	if recycle == "" {
		recycle = RecycleNone
	}
	if !knownRecycleReason(recycle) {
		return invalid, RecycleProtocolFailure
	}
	return &ProviderFailure{Code: failure.Code, Disposition: failure.Disposition}, recycle
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

func knownRecycleReason(reason RecycleReason) bool {
	switch reason {
	case RecycleNone, RecycleProcessAge, RecycleRSSLimit, RecycleSessionLeak,
		RecycleTargetLeak, RecycleProcessCrash, RecycleFailedHealth,
		RecycleCleanupFailure, RecycleProtocolFailure, RecycleTaskLimit:
		return true
	default:
		return false
	}
}

func failureResult(
	code runtimev1.ErrorCode,
	disposition runtimev1.ErrorDisposition,
) *runtimev1.BrowserResult {
	message := "browser execution failed"
	switch code {
	case runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG:
		message = "browser configuration rejected"
	case runtimev1.ErrorCode_ERROR_CODE_TIMEOUT:
		message = "browser execution timed out"
	case runtimev1.ErrorCode_ERROR_CODE_CANCELLED:
		message = "browser execution cancelled"
	case runtimev1.ErrorCode_ERROR_CODE_TARGET_LOST:
		message = "browser target lost"
	case runtimev1.ErrorCode_ERROR_CODE_SESSION_LOST:
		message = "browser session lost"
	case runtimev1.ErrorCode_ERROR_CODE_TRANSPORT:
		message = "browser transport failed"
	case runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT:
		message = "browser resource limit reached"
	case runtimev1.ErrorCode_ERROR_CODE_NAVIGATION:
		message = "browser navigation failed"
	}
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersionV1,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM,
		Outcome: &runtimev1.BrowserResult_Error{Error: &runtimev1.BrowserFailure{
			Error: &runtimev1.RuntimeError{
				Code:        code,
				Disposition: disposition,
				Message:     message,
			},
		}},
	}
}

func unsupportedResult(capabilities []runtimev1.BrowserCapability) *runtimev1.BrowserResult {
	return &runtimev1.BrowserResult{
		ContractVersion: runtimeContractVersionV1,
		Backend:         runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM,
		Outcome: &runtimev1.BrowserResult_Unsupported{Unsupported: &runtimev1.BrowserUnsupported{
			Capabilities: append([]runtimev1.BrowserCapability(nil), capabilities...),
		}},
	}
}
