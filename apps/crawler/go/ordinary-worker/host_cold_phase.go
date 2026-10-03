package worker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io/fs"
	"path/filepath"
	"strconv"
	"sync"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Journal stages include the native B0 transfer and publication primitives.
// Release selection and startup require additional authenticated host phases.
// Inputs are content hashes in the existing private host store, never paths,
// connection URLs, shell commands, environment maps or a request for "latest".
type HostColdPhaseRequest struct {
	Version                       string                   `json:"version"`
	Binding                       queue.HostColdSQLBinding `json:"binding"`
	RedisEndpointSHA256           string                   `json:"redis_endpoint_sha256"`
	RedisInstanceSHA256           string                   `json:"redis_instance_sha256"`
	PredecessorSHA256             string                   `json:"predecessor_result_sha256,omitempty"`
	Operation                     string                   `json:"operation"`
	PreviousEpoch                 int64                    `json:"previous_epoch"`
	IntentSHA256                  string                   `json:"intent_sha256,omitempty"`
	TargetSHA256                  string                   `json:"target_sha256,omitempty"`
	LuaSHA256                     string                   `json:"lua_sha256,omitempty"`
	Namespace                     string                   `json:"namespace,omitempty"`
	Shard                         string                   `json:"shard,omitempty"`
	Cohort                        string                   `json:"cohort,omitempty"`
	RoutingEpoch                  int64                    `json:"routing_epoch,omitempty"`
	PlanSHA256                    string                   `json:"plan_sha256,omitempty"`
	ForwardRequestSHA256          string                   `json:"forward_request_sha256,omitempty"`
	ForwardPlanSHA256             string                   `json:"forward_plan_sha256,omitempty"`
	ForwardReceiptSHA256          string                   `json:"forward_receipt_sha256,omitempty"`
	ReversalSHA256                string                   `json:"reversal_sha256,omitempty"`
	RetirementEpoch               int64                    `json:"retirement_epoch,omitempty"`
	RestoreRequestSHA256          string                   `json:"restore_request_sha256,omitempty"`
	B0RollbackPlanSHA256          string                   `json:"b0_rollback_plan_sha256,omitempty"`
	OrdinaryRequestSHA256         string                   `json:"ordinary_request_sha256,omitempty"`
	OrdinaryRestorationPlanSHA256 string                   `json:"ordinary_restoration_plan_sha256,omitempty"`
	PriorB0ReceiptSHA256          string                   `json:"prior_b0_receipt_sha256,omitempty"`
	B0ReactivationPlanSHA256      string                   `json:"b0_reactivation_plan_sha256,omitempty"`
	B0ReactivationReceiptSHA256   string                   `json:"b0_reactivation_receipt_sha256,omitempty"`
	FinalizationRequestSHA256     string                   `json:"finalization_request_sha256,omitempty"`
	FinalizationPlanSHA256        string                   `json:"finalization_plan_sha256,omitempty"`
}

type HostColdPhaseResult struct {
	Version           string                   `json:"version"`
	Binding           queue.HostColdSQLBinding `json:"binding"`
	RequestSHA256     string                   `json:"request_sha256"`
	PredecessorSHA256 string                   `json:"predecessor_result_sha256,omitempty"`
	Outcome           string                   `json:"outcome"`
	Native            *ColdAdminIdentity       `json:"native,omitempty"`
	RuntimeAdmission  bool                     `json:"runtime_admission"`
}

// File evidence hashes bind the previously verified active/incoming/rollback
// generation bundles. The attestation is stable across SQL backend retries;
// its private constructor observes the live session and actual cold containers.
// Serializing this context does not preserve the live host/SQL authority.
type HostColdPhaseContext struct {
	Version               string                   `json:"version"`
	Binding               queue.HostColdSQLBinding `json:"binding"`
	ActiveReleaseSHA256   string                   `json:"active_release_sha256"`
	TargetReleaseSHA256   string                   `json:"target_release_sha256"`
	RollbackReleaseSHA256 string                   `json:"rollback_release_sha256"`
	ColdAttestationSHA256 string                   `json:"cold_attestation_sha256"`
	RuntimeAdmission      bool                     `json:"runtime_admission"`
	RedisEndpointSHA256   string                   `json:"redis_endpoint_sha256,omitempty"`
	RedisInstanceSHA256   string                   `json:"redis_instance_sha256,omitempty"`
}

type hostColdPhaseKey struct{}
type hostColdPhaseScope struct {
	mu     sync.Mutex
	active bool
	store  *hostStore
	pool   *pgxpool.Pool
	info   HostColdPhaseContext
	guard  func() error
	redis  hostRedisResolver
}

func withHostColdPhaseScope(ctx context.Context, store *hostStore, pool *pgxpool.Pool, sql *queue.HostColdSQL, binding queue.HostColdSQLBinding, releases []HostReleaseRequest, coldSHA string, guard func() error, fn func(context.Context) error) error {
	if store == nil || sql == nil || guard == nil || fn == nil || len(releases) != 3 || !planPattern.MatchString(coldSHA) || queue.CheckHostColdSQLBinding(ctx, pool, binding) != nil || guard() != nil || ctx.Value(hostColdPhaseKey{}) != nil {
		return errHostPreflight
	}
	for n, role := range []string{"active", "incoming", "rollback"} {
		if releases[n].Role != role || !planPattern.MatchString(releases[n].FileEvidenceSHA256) {
			return errHostPreflight
		}
	}
	var observation struct {
		Database string  `json:"database_sha256"`
		Keys     []int64 `json:"exclusive_barriers"`
	}
	if json.Unmarshal([]byte(sql.Body()), &observation) != nil || !planPattern.MatchString(observation.Database) || len(observation.Keys) != 3 {
		return errHostPreflight
	}
	info := HostColdPhaseContext{Version: "jobseek.crawler-host-cold-context/v1", Binding: binding, ActiveReleaseSHA256: releases[0].FileEvidenceSHA256, TargetReleaseSHA256: releases[1].FileEvidenceSHA256, RollbackReleaseSHA256: releases[2].FileEvidenceSHA256}
	attestation, err := json.Marshal(struct {
		Version        string               `json:"version"`
		Context        HostColdPhaseContext `json:"context"`
		ColdContainers string               `json:"cold_containers_sha256"`
		Database       string               `json:"database_sha256"`
		ExclusiveKeys  []int64              `json:"exclusive_barriers"`
		LiveBoardLease int                  `json:"live_board_leases"`
		LiveJobLease   int                  `json:"live_posting_leases"`
	}{"jobseek.crawler-host-cold-attestation/v1", info, coldSHA, observation.Database, observation.Keys, 0, 0})
	if err != nil {
		return errHostPreflight
	}
	info.ColdAttestationSHA256 = hostDigest(attestation)
	s := &hostColdPhaseScope{active: true, store: store, pool: pool, info: info, guard: guard}
	defer func() { s.mu.Lock(); s.active = false; s.mu.Unlock() }()
	return fn(context.WithValue(ctx, hostColdPhaseKey{}, s))
}

func (s *hostColdPhaseScope) check(ctx context.Context, pool *pgxpool.Pool) error {
	if s == nil || !s.active || s.pool != pool || ctx == nil || ctx.Err() != nil || ctx.Value(hostColdPhaseKey{}) != s || s.store.verify() != nil || queue.CheckHostColdSQLBinding(ctx, pool, s.info.Binding) != nil || s.guard() != nil {
		return errHostPreflight
	}
	return nil
}

// InspectHostColdPhaseContext reports the bindings of the currently held scope.
// Its result is evidence, not a token for a later invocation.
func InspectHostColdPhaseContext(ctx context.Context, pool *pgxpool.Pool) (HostColdPhaseContext, error) {
	if ctx == nil {
		return HostColdPhaseContext{}, errHostPreflight
	}
	s, _ := ctx.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
	if s == nil {
		return HostColdPhaseContext{}, errHostPreflight
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.check(ctx, pool) != nil {
		return HostColdPhaseContext{}, errHostPreflight
	}
	info := s.info
	if r, _ := ctx.Value(hostColdRedisKey{}).(*hostColdRedisScope); r != nil {
		if r.check(ctx, s) != nil {
			return HostColdPhaseContext{}, errHostPreflight
		}
		info.RedisEndpointSHA256, info.RedisInstanceSHA256 = r.endpointSHA, r.instanceSHA
	}
	return info, nil
}

func decodeHostColdPhase(body []byte, sha string, binding queue.HostColdSQLBinding) (HostColdPhaseRequest, error) {
	var r HostColdPhaseRequest
	if len(body) > 4096 || !planPattern.MatchString(sha) || hostDigest(body) != sha || canonicalHostDecode(body, &r) != nil || r.Version != "jobseek.crawler-host-cold-request/v2" || r.Binding != binding || !planPattern.MatchString(r.RedisEndpointSHA256) || !planPattern.MatchString(r.RedisInstanceSHA256) || r.PreviousEpoch < 1 || r.PreviousEpoch >= 9999999999999 || r.PredecessorSHA256 != "" && !planPattern.MatchString(r.PredecessorSHA256) {
		return r, errHostPreflight
	}
	switch r.Operation {
	case "cold-b0-target":
		if !planPattern.MatchString(r.LuaSHA256) || r.IntentSHA256 != "" || r.TargetSHA256 != "" || r.Namespace == "" || r.Shard == "" || r.Cohort == "" {
			return r, errHostPreflight
		}
	case "cold-begin":
		if !planPattern.MatchString(r.LuaSHA256) || !planPattern.MatchString(r.IntentSHA256) || !planPattern.MatchString(r.TargetSHA256) || r.Namespace != "" || r.Shard != "" || r.Cohort != "" {
			return r, errHostPreflight
		}
	case "cold-reserve", "cold-inspect":
		if !planPattern.MatchString(r.IntentSHA256) || r.TargetSHA256 != "" || r.LuaSHA256 != "" || r.Namespace != "" || r.Shard != "" || r.Cohort != "" {
			return r, errHostPreflight
		}
	default:
		if hostColdCompletionOperation(r.Operation) {
			if validateHostColdCompletionRequest(r) != nil {
				return r, errHostPreflight
			}
		} else if hostColdRestorationOperation(r.Operation) {
			if validateHostColdRestorationRequest(r) != nil {
				return r, errHostPreflight
			}
		} else if hostColdReversalOperation(r.Operation) {
			if validateHostColdReversalRequest(r) != nil {
				return r, errHostPreflight
			}
		} else if validateHostColdForwardRequest(r) != nil {
			return r, errHostPreflight
		}
	}
	if !coldB0ForwardOperation(r.Operation) && !hostColdRetirementOperation(r.Operation) && (r.RoutingEpoch != 0 || r.PlanSHA256 != "" || r.ForwardRequestSHA256 != "" || r.ForwardPlanSHA256 != "" || r.ForwardReceiptSHA256 != "") {
		return r, errHostPreflight
	}
	if !hostColdRetirementOperation(r.Operation) && (r.ReversalSHA256 != "" || r.RetirementEpoch != 0) || !hostColdRestorationOperation(r.Operation) && !hostColdCompletionOperation(r.Operation) && !hostColdRestorationInputsEmpty(r) || !hostColdCompletionOperation(r.Operation) && !hostColdCompletionInputsEmpty(r) {
		return r, errHostPreflight
	}
	return r, nil
}

type hostColdPhaseCompletion struct {
	Version       string                   `json:"version"`
	Binding       queue.HostColdSQLBinding `json:"binding"`
	RequestSHA256 string                   `json:"request_sha256"`
	ResultSHA256  string                   `json:"result_sha256"`
}

func (s *hostColdPhaseScope) readResult(requestSHA string, pending bool) (*HostColdPhaseResult, []byte, error) {
	request, err := s.store.read("cold-request-"+requestSHA+".json", false)
	rq, decodeErr := decodeHostColdPhase(request, requestSHA, s.info.Binding)
	if err != nil || decodeErr != nil {
		return nil, nil, errHostPreflight
	}
	body, err := s.store.readColdPhaseLimit("cold-result-"+requestSHA+".json", pending, hostColdResultLimit(rq.Operation))
	if err != nil {
		return nil, nil, err
	}
	var r HostColdPhaseResult
	if canonicalHostDecode(body, &r) != nil || r.Version != "jobseek.crawler-host-cold-result/v1" || r.Binding != s.info.Binding || r.RequestSHA256 != requestSHA || r.RuntimeAdmission || r.Outcome != "completed" && r.Outcome != "unresolved" || r.Outcome == "completed" && r.Native == nil || r.Outcome == "unresolved" && r.Native != nil {
		return nil, nil, errHostPreflight
	}
	return &r, body, nil
}

func (s *hostColdPhaseScope) predecessor(r HostColdPhaseRequest) (*HostColdPhaseRequest, *HostColdPhaseResult, error) {
	prior, parent, err := s.loadPredecessor(r)
	if err != nil || parent == nil {
		return prior, parent, err
	}
	if parent.Outcome == "unresolved" {
		return prior, parent, nil
	}
	switch r.Operation {
	case "cold-begin":
		if prior.Operation != "cold-b0-target" || r.TargetSHA256 != parent.Native.B0TargetSHA256 || r.LuaSHA256 != prior.LuaSHA256 {
			return nil, nil, errHostPreflight
		}
	case "cold-reserve":
		if prior.Operation != "cold-begin" || r.IntentSHA256 != prior.IntentSHA256 || parent.Native.IntentSHA256 != r.IntentSHA256 {
			return nil, nil, errHostPreflight
		}
	case "cold-inspect":
		if prior.Operation != "cold-reserve" || r.IntentSHA256 != prior.IntentSHA256 || parent.Native.IntentSHA256 != r.IntentSHA256 || parent.Native.RoutingEpoch <= r.PreviousEpoch || !planPattern.MatchString(parent.Native.PlanSHA256) {
			return nil, nil, errHostPreflight
		}
	default:
		if hostColdCompletionOperation(r.Operation) {
			if checkHostColdCompletionLink(r, *prior, parent) != nil || s.retirementAncestry(r) != nil {
				return nil, nil, errHostPreflight
			}
		} else if hostColdRestorationOperation(r.Operation) {
			if s.restorationPredecessor(r, *prior, parent) != nil {
				return nil, nil, errHostPreflight
			}
		} else if hostColdReversalOperation(r.Operation) {
			if s.reversalPredecessor(r, *prior, parent) != nil {
				return nil, nil, errHostPreflight
			}
		} else if s.forwardPredecessor(r, *prior, parent) != nil {
			return nil, nil, errHostPreflight
		}
	}
	return prior, parent, nil
}

// loadPredecessor verifies the immutable request/result/completion join. It
// grants no successor authority; predecessor applies the operation policy.
func (s *hostColdPhaseScope) loadPredecessor(r HostColdPhaseRequest) (*HostColdPhaseRequest, *HostColdPhaseResult, error) {
	if r.PredecessorSHA256 == "" {
		if r.Operation != "cold-b0-target" {
			return nil, nil, errHostPreflight
		}
		return nil, nil, nil
	}
	index, err := s.store.read("cold-predecessor-"+r.PredecessorSHA256+".json", false)
	var c hostColdPhaseCompletion
	if err != nil || canonicalHostDecode(index, &c) != nil || c.Version != "jobseek.crawler-host-cold-completion/v1" || c.Binding != s.info.Binding || c.ResultSHA256 != r.PredecessorSHA256 || !planPattern.MatchString(c.RequestSHA256) {
		return nil, nil, errHostPreflight
	}
	parent, body, err := s.readResult(c.RequestSHA256, false)
	completed, e := s.store.read("cold-completed-"+c.RequestSHA256+".json", false)
	request, re := s.store.read("cold-request-"+c.RequestSHA256+".json", false)
	prior, de := decodeHostColdPhase(request, c.RequestSHA256, s.info.Binding)
	if err != nil || e != nil || re != nil || de != nil || hostDigest(body) != c.ResultSHA256 || !bytes.Equal(index, completed) || parent.PredecessorSHA256 != prior.PredecessorSHA256 || prior.PreviousEpoch != r.PreviousEpoch || prior.RedisEndpointSHA256 != r.RedisEndpointSHA256 || prior.RedisInstanceSHA256 != r.RedisInstanceSHA256 {
		return nil, nil, errHostPreflight
	}
	if parent.Outcome == "unresolved" {
		// Retry is a separate journal event with the exact native inputs. An
		// unresolved outcome must never authorize the next kind of effect.
		a, b := prior, r
		a.PredecessorSHA256, b.PredecessorSHA256 = "", ""
		if a != b {
			return nil, nil, errHostPreflight
		}
	} else {
		if parent.Native == nil || parent.Native.SourceRevision != r.Binding.SourceRevision || parent.Native.Operation != prior.Operation {
			return nil, nil, errHostPreflight
		}
	}
	return &prior, parent, nil
}

func (s *hostColdPhaseScope) config(r HostColdPhaseRequest) (ColdAdminConfig, map[string][]byte, error) {
	inputs := map[string][]byte{}
	path := func(sha string, limit int) (string, error) {
		name := "cold-input-" + sha
		body, err := s.store.read(name, true)
		if err != nil || len(body) > limit || hostDigest(body) != sha || s.store.retain(name, body, nil) != nil {
			return "", errHostPreflight
		}
		inputs[name] = body
		return filepath.Join(s.store.path, name), nil
	}
	env := map[string]string{"LOCAL_DATABASE_URL": "host-bound-connection", "REDIS_URL": "host-bound-connection", "ORDINARY_GO_WORKER_MODE": r.Operation, "ORDINARY_OWNERSHIP_SOURCE_REVISION": r.Binding.SourceRevision, "ORDINARY_COLD_ROUTING_EPOCH": strconv.FormatInt(r.PreviousEpoch, 10), "ORDINARY_COLD_B0_NAMESPACE": r.Namespace, "ORDINARY_COLD_B0_SHARD_ID": r.Shard, "ORDINARY_COLD_B0_COHORT": r.Cohort}
	if coldB0ForwardOperation(r.Operation) || hostColdRetirementOperation(r.Operation) {
		env["ORDINARY_COLD_ROUTING_EPOCH"] = strconv.FormatInt(r.RoutingEpoch, 10)
		env["ORDINARY_COLD_PLAN_SHA256"] = r.PlanSHA256
	}
	if coldB0ForwardOperation(r.Operation) {
		env["ORDINARY_COLD_B0_FORWARD_PLAN_SHA256"] = r.ForwardPlanSHA256
		env["ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256"] = r.ForwardReceiptSHA256
	}
	if hostColdRestorationOperation(r.Operation) {
		env["ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256"] = r.B0RollbackPlanSHA256
		env["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = r.OrdinaryRestorationPlanSHA256
	}
	if hostColdCompletionOperation(r.Operation) {
		env["ORDINARY_COLD_RETIREMENT_EPOCH"] = strconv.FormatInt(r.RetirementEpoch, 10)
		env["ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256"] = r.OrdinaryRestorationPlanSHA256
		env["ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"] = r.B0ReactivationPlanSHA256
		env["ORDINARY_COLD_FINALIZATION_PLAN_SHA256"] = r.FinalizationPlanSHA256
	}
	for _, input := range []struct {
		hash, fileKey, hashKey string
		limit                  int
	}{
		{r.LuaSHA256, "ORDINARY_COLD_B0_LUA_FILE", "", 128 << 10},
		{r.IntentSHA256, "ORDINARY_COLD_INTENT_FILE", "ORDINARY_COLD_INTENT_SHA256", 4096},
		{r.TargetSHA256, "ORDINARY_COLD_B0_TARGET_FILE", "ORDINARY_COLD_B0_TARGET_SHA256", 16384},
		{r.ForwardRequestSHA256, "ORDINARY_COLD_B0_FORWARD_REQUEST_FILE", "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256", 4096},
		{r.ReversalSHA256, "ORDINARY_COLD_REVERSAL_FILE", "ORDINARY_COLD_REVERSAL_SHA256", 4096},
		{r.RestoreRequestSHA256, "ORDINARY_COLD_B0_RESTORE_REQUEST_FILE", "ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256", 4096},
		{r.OrdinaryRequestSHA256, "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256", 4096},
		{r.PriorB0ReceiptSHA256, "ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256", 4096},
		{r.FinalizationRequestSHA256, "ORDINARY_COLD_FINALIZATION_REQUEST_FILE", "ORDINARY_COLD_FINALIZATION_REQUEST_SHA256", 4096},
	} {
		if input.hash == "" {
			continue
		}
		p, err := path(input.hash, input.limit)
		if err != nil {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
		env[input.fileKey] = p
		if input.hashKey != "" {
			env[input.hashKey] = input.hash
		}
	}
	if coldB0ForwardOperation(r.Operation) {
		forward, err := queue.DecodeColdB0ForwardRequest(string(inputs["cold-input-"+r.ForwardRequestSHA256]), r.ForwardRequestSHA256)
		if err != nil || forward != (queue.ColdB0ForwardRequest{IntentSHA256: r.IntentSHA256, SourceRevision: r.Binding.SourceRevision, RoutingEpoch: r.RoutingEpoch, OrdinaryPlanSHA256: r.PlanSHA256}) {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
	}
	if r.IntentSHA256 != "" {
		spec, err := queue.DecodeColdTransitionSpec(string(inputs["cold-input-"+r.IntentSHA256]), r.IntentSHA256)
		if err != nil || spec.SourceRevision != r.Binding.SourceRevision || spec.PreviousEpoch != r.PreviousEpoch || spec.ActiveReleaseSHA256 != s.info.ActiveReleaseSHA256 || spec.TargetReleaseSHA256 != s.info.TargetReleaseSHA256 || spec.RollbackReleaseSHA256 != s.info.RollbackReleaseSHA256 || spec.ColdAttestationSHA256 != s.info.ColdAttestationSHA256 || r.Operation == "cold-begin" && spec.TargetB0ManifestSHA256 != r.TargetSHA256 {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
		if hostColdRetirementOperation(r.Operation) && s.checkReversalSpec(r, spec, inputs) != nil {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
		if hostColdRestorationOperation(r.Operation) && validateHostColdRestorationInputs(r, spec, inputs) != nil {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
		if coldB0RestorationOperation(r.Operation) && s.checkRestorationSourceReceipt(r, spec, inputs) != nil {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
		if hostColdCompletionOperation(r.Operation) && s.checkCompletionInputs(r, spec, inputs) != nil {
			return ColdAdminConfig{}, nil, errHostPreflight
		}
	}
	c, err := ReadColdAdminConfig(func(key string) string { return env[key] }, r.Binding.SourceRevision, r.Operation)
	return c, inputs, err
}

func validateHostColdPhaseResult(r HostColdPhaseRequest, result *HostColdPhaseResult, inputs map[string][]byte, parent *HostColdPhaseResult) error {
	if result == nil || result.Binding != r.Binding || result.PredecessorSHA256 != r.PredecessorSHA256 || result.RuntimeAdmission {
		return errHostPreflight
	}
	if result.Outcome == "unresolved" && result.Native == nil {
		return nil
	}
	n := result.Native
	if result.Outcome != "completed" || n == nil || n.Version != "jobseek.ordinary.cold-identity/v1" || n.Operation != r.Operation || n.SourceRevision != r.Binding.SourceRevision {
		return errHostPreflight
	}
	switch r.Operation {
	case "cold-b0-target":
		if n.RoutingEpoch != r.PreviousEpoch || n.IntentSHA256 != "" {
			return errHostPreflight
		}
		if _, err := queue.DecodeColdB0Target(string(n.Target), n.B0TargetSHA256, inputs["cold-input-"+r.LuaSHA256]); err != nil {
			return errHostPreflight
		}
	case "cold-begin":
		if n.IntentSHA256 != r.IntentSHA256 || n.B0TargetSHA256 != r.TargetSHA256 || n.RoutingEpoch != 0 {
			return errHostPreflight
		}
	case "cold-reserve", "cold-inspect":
		if n.IntentSHA256 != r.IntentSHA256 || n.RoutingEpoch <= r.PreviousEpoch || !planPattern.MatchString(n.PlanSHA256) {
			return errHostPreflight
		}
		if r.Operation == "cold-inspect" && (parent == nil || parent.Native == nil || n.PlanSHA256 != parent.Native.PlanSHA256 || n.RoutingEpoch != parent.Native.RoutingEpoch) {
			return errHostPreflight
		}
	default:
		if hostColdCompletionOperation(r.Operation) {
			return validateHostColdCompletionResult(r, n, inputs, parent)
		}
		if hostColdRestorationOperation(r.Operation) {
			return validateHostColdRestorationResult(r, n, inputs, parent)
		}
		if hostColdReversalOperation(r.Operation) {
			return validateHostColdReversalResult(r, n, parent)
		}
		return validateHostColdForwardResult(r, n, parent)
	}
	return nil
}

// RunHostColdPhase executes one hashed, protected request within the callback
// provided by WithHostQuiescence and WithSelectedHostColdRedis. No caller client
// or URL can replace the scope's verified selected Redis connection.
// A completed retry returns retained historical bytes, not current admission.
func RunHostColdPhase(ctx context.Context, pool *pgxpool.Pool, requestSHA string) (*HostColdPhaseResult, error) {
	return runHostColdPhase(ctx, pool, requestSHA, nil)
}

func runHostColdPhase(ctx context.Context, pool *pgxpool.Pool, requestSHA string, hook func(string) error) (*HostColdPhaseResult, error) {
	if ctx == nil || !planPattern.MatchString(requestSHA) {
		return nil, errHostPreflight
	}
	s, _ := ctx.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
	if s == nil {
		return nil, errHostPreflight
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	redis, _ := ctx.Value(hostColdRedisKey{}).(*hostColdRedisScope)
	if s.check(ctx, pool) != nil || redis == nil || redis.check(ctx, s) != nil {
		return nil, errHostPreflight
	}
	requestName := "cold-request-" + requestSHA + ".json"
	body, err := s.store.read(requestName, true)
	r, errDecode := decodeHostColdPhase(body, requestSHA, s.info.Binding)
	if err != nil || errDecode != nil || r.RedisEndpointSHA256 != redis.endpointSHA || r.RedisInstanceSHA256 != redis.instanceSHA {
		return nil, errHostPreflight
	}
	prior, parent, err := s.predecessor(r)
	if err != nil {
		return nil, errHostPreflight
	}
	// An inspection retry must still join the original completed reservation,
	// rather than acquiring authority from an unresolved inspection event.
	for depth := 0; parent != nil && parent.Outcome == "unresolved"; depth++ {
		if prior == nil || depth >= 64 {
			return nil, errHostPreflight
		}
		prior, parent, err = s.predecessor(*prior)
		if err != nil {
			return nil, errHostPreflight
		}
	}
	c, inputs, err := s.config(r)
	if err != nil {
		return nil, errHostPreflight
	}
	guard := func() error {
		if s.check(ctx, pool) != nil || redis.check(ctx, s) != nil {
			return errHostPreflight
		}
		current, err := s.store.read(requestName, false)
		if err != nil || !bytes.Equal(current, body) {
			return errHostPreflight
		}
		for name, expected := range inputs {
			b, err := s.store.read(name, false)
			if err != nil || !bytes.Equal(b, expected) {
				return errHostPreflight
			}
		}
		return nil
	}
	claim := "cold-start.json"
	if r.PredecessorSHA256 != "" {
		claim = "cold-next-" + r.PredecessorSHA256 + ".json"
	}
	claimBody, _ := json.Marshal(struct {
		Version       string `json:"version"`
		RequestSHA256 string `json:"request_sha256"`
	}{"jobseek.crawler-host-cold-child/v1", requestSHA})
	if s.store.retain(requestName, body, nil) != nil || guard() != nil || s.store.retain(claim, claimBody, nil) != nil || s.store.retain("cold-intent-"+requestSHA+".json", body, nil) != nil || guard() != nil {
		return nil, errHostPreflight
	}
	if hook != nil && hook("phase_intent_retained") != nil {
		return nil, errHostPreflight
	}
	result, resultBody, err := s.readResult(requestSHA, true)
	if errors.Is(err, fs.ErrNotExist) {
		native, effectErr := RunColdAdminInHostScope(ctx, c, pool, redis.client)
		if hook != nil && hook("native_effect_returned") != nil {
			return nil, errHostPreflight
		}
		if guard() != nil {
			return nil, errHostPreflight
		}
		outcome := "completed"
		if effectErr != nil {
			outcome, native = "unresolved", nil
		}
		result = &HostColdPhaseResult{"jobseek.crawler-host-cold-result/v1", r.Binding, requestSHA, r.PredecessorSHA256, outcome, native, false}
		resultBody, err = json.Marshal(result)
		if err != nil {
			return nil, errHostPreflight
		}
	} else if err != nil {
		return nil, errHostPreflight
	}
	if validateHostColdPhaseResult(r, result, inputs, parent) != nil {
		return nil, errHostPreflight
	}
	if result.Native != nil && r.Operation == "cold-b0-target" {
		if len(result.Native.Target) == 0 || hostDigest(result.Native.Target) != result.Native.B0TargetSHA256 || s.store.retain("cold-input-"+result.Native.B0TargetSHA256, result.Native.Target, nil) != nil {
			return nil, errHostPreflight
		}
	}
	if result.Native != nil && len(result.Native.B0ForwardPlan) != 0 {
		if s.store.retainLimit("cold-input-"+result.Native.B0ForwardPlanSHA256, result.Native.B0ForwardPlan, nil, 32<<20) != nil {
			return nil, errHostPreflight
		}
	}
	if result.Native != nil {
		for _, plan := range []struct {
			sha  string
			body []byte
		}{
			{result.Native.B0RollbackPlanSHA256, result.Native.B0RollbackPlan},
			{result.Native.OrdinaryRestorationPlanSHA256, result.Native.OrdinaryRestorationPlan},
		} {
			if len(plan.body) != 0 && s.store.retainLimit("cold-input-"+plan.sha, plan.body, nil, 32<<20) != nil {
				return nil, errHostPreflight
			}
		}
		for _, plan := range []struct {
			sha   string
			body  []byte
			limit int64
		}{
			{result.Native.B0ReactivationPlanSHA256, result.Native.B0ReactivationPlan, 64 << 20},
			{result.Native.OrdinaryFinalizationPlanSHA256, result.Native.OrdinaryFinalizationPlan, 32 << 20},
		} {
			if len(plan.body) != 0 && s.store.retainColdPhaseLimit("cold-input-"+plan.sha, plan.body, nil, plan.limit) != nil {
				return nil, errHostPreflight
			}
		}
	}
	if guard() != nil || s.store.retainColdPhaseLimit("cold-result-"+requestSHA+".json", resultBody, hook, hostColdResultLimit(r.Operation)) != nil || guard() != nil {
		return nil, errHostPreflight
	}
	if hook != nil && hook("phase_result_retained") != nil {
		return nil, errHostPreflight
	}
	completion, _ := json.Marshal(hostColdPhaseCompletion{"jobseek.crawler-host-cold-completion/v1", r.Binding, requestSHA, hostDigest(resultBody)})
	if s.store.retain("cold-completed-"+requestSHA+".json", completion, nil) != nil || s.store.retain("cold-predecessor-"+hostDigest(resultBody)+".json", completion, nil) != nil || guard() != nil {
		return nil, errHostPreflight
	}
	if hook != nil && hook("phase_completed_retained") != nil {
		return nil, errHostPreflight
	}
	// Retained failures deliberately have no native identity and no zero-effect
	// assertion. A new exact-input retry event must name this result as parent.
	return result, nil
}
