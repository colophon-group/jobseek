package worker

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ColdAdminOperation recognizes only explicit one-shot coordinator arguments.
func ColdAdminOperation(argument string) string {
	switch argument {
	case "--cold-b0-target", "--cold-begin", "--cold-reserve", "--cold-inspect", "--cold-prepare", "--cold-publish", "--cold-activate", "--cold-reversal-begin", "--cold-reversal-reserve", "--cold-reversal-inspect":
		return strings.TrimPrefix(argument, "--")
	case "--cold-ordinary-rollback-plan", "--cold-ordinary-rollback-retain", "--cold-ordinary-rollback-inspect":
		return strings.TrimPrefix(argument, "--")
	case "--cold-b0-reactivation-plan", "--cold-b0-reactivation-retain", "--cold-b0-reactivation-apply", "--cold-b0-reactivation-inspect":
		return strings.TrimPrefix(argument, "--")
	case "--cold-forward-prepare", "--cold-forward-publish", "--cold-forward-activate":
		return strings.TrimPrefix(argument, "--")
	case "--cold-b0-forward-plan", "--cold-b0-forward-retain", "--cold-b0-forward-apply", "--cold-b0-forward-inspect":
		return strings.TrimPrefix(argument, "--")
	case "--cold-b0-rollback-plan", "--cold-b0-rollback-retain", "--cold-b0-rollback-restore", "--cold-b0-rollback-inspect":
		return strings.TrimPrefix(argument, "--")
	}
	return ""
}

func coldReversalOperation(operation string) bool {
	return operation == "cold-reversal-begin" || operation == "cold-reversal-reserve" || operation == "cold-reversal-inspect" || coldB0RestorationOperation(operation) || coldOrdinaryRestorationOperation(operation)
}

func coldOrdinaryRestorationOperation(operation string) bool {
	return operation == "cold-ordinary-rollback-plan" || operation == "cold-ordinary-rollback-retain" || operation == "cold-ordinary-rollback-inspect"
}

func coldB0RestorationOperation(operation string) bool {
	return operation == "cold-b0-rollback-plan" || operation == "cold-b0-rollback-retain" || operation == "cold-b0-rollback-restore" || operation == "cold-b0-rollback-inspect"
}

func coldForwardPublicationOperation(operation string) bool {
	return operation == "cold-forward-prepare" || operation == "cold-forward-publish" || operation == "cold-forward-activate"
}

func coldB0ForwardOperation(operation string) bool {
	return coldForwardPublicationOperation(operation) || operation == "cold-b0-forward-plan" || operation == "cold-b0-forward-retain" || operation == "cold-b0-forward-apply" || operation == "cold-b0-forward-inspect"
}

type ColdAdminConfig struct {
	database, redis, source, operation                              string
	intentFile, intentSHA, targetFile, targetSHA, luaFile           string
	planSHA, namespace, shard, cohort                               string
	epoch                                                           int64
	reversalFile, reversalSHA                                       string
	restoreRequestFile, restoreRequestSHA, restorePlanSHA           string
	forwardRequestFile, forwardRequestSHA, forwardPlanSHA           string
	forwardReceiptSHA                                               string
	ordinaryRequestFile, ordinaryRequestSHA, ordinaryRestorationSHA string
	priorB0ReceiptFile, priorB0ReceiptSHA, reactivationPlanSHA      string
	retirementEpoch                                                 int64
}

// ColdAdminIdentity reports the completed primitive, not host release readiness
// or a grant to start services. Target contains only bounded board identities and
// configuration digests; the host must durably retain its exact canonical bytes.
type ColdAdminIdentity struct {
	Version                       string          `json:"version"`
	Operation                     string          `json:"operation"`
	SourceRevision                string          `json:"source_revision"`
	IntentSHA256                  string          `json:"intent_sha256,omitempty"`
	RoutingEpoch                  int64           `json:"routing_epoch,omitempty"`
	PlanSHA256                    string          `json:"plan_sha256,omitempty"`
	ProjectionSHA1                string          `json:"projection_sha1,omitempty"`
	Members                       int             `json:"members,omitempty"`
	B0TargetSHA256                string          `json:"b0_target_sha256,omitempty"`
	Target                        json.RawMessage `json:"target,omitempty"`
	RetainedPhase                 string          `json:"retained_phase,omitempty"`
	ReversalSHA256                string          `json:"reversal_sha256,omitempty"`
	ReversalPhase                 string          `json:"reversal_phase,omitempty"`
	RetirementEpoch               int64           `json:"retirement_routing_epoch,omitempty"`
	B0RollbackPlanSHA256          string          `json:"b0_rollback_plan_sha256,omitempty"`
	B0RollbackPlan                json.RawMessage `json:"b0_rollback_plan,omitempty"`
	B0ForwardPhase                string          `json:"b0_forward_phase,omitempty"`
	B0ForwardReceiptSHA256        string          `json:"b0_forward_receipt_sha256,omitempty"`
	B0ForwardPlanSHA256           string          `json:"b0_forward_plan_sha256,omitempty"`
	B0ForwardPlan                 json.RawMessage `json:"b0_forward_plan,omitempty"`
	OrdinaryRestorationPlanSHA256 string          `json:"ordinary_restoration_plan_sha256,omitempty"`
	OrdinaryRestorationPlan       json.RawMessage `json:"ordinary_restoration_plan,omitempty"`
	OrdinaryRestorationMode       string          `json:"ordinary_restoration_mode,omitempty"`
	B0RestorationPhase            string          `json:"b0_restoration_phase,omitempty"`
	B0ReactivationPlanSHA256      string          `json:"b0_reactivation_plan_sha256,omitempty"`
	B0ReactivationPlan            json.RawMessage `json:"b0_reactivation_plan,omitempty"`
	B0ReactivationPhase           string          `json:"b0_reactivation_phase,omitempty"`
	B0ReactivationReceiptSHA256   string          `json:"b0_reactivation_receipt_sha256,omitempty"`
}

// ReadColdAdminConfig requires a distinct protected mode, compiled source,
// explicit epoch and exact input hashes. Worker/staging environment fields cannot
// select a coordinator operation. No caller can request "latest" authority.
func ReadColdAdminConfig(getenv func(string) string, installed, operation string) (ColdAdminConfig, error) {
	var c ColdAdminConfig
	if getenv == nil || ColdAdminOperation("--"+operation) != operation || operation == "" || !sourcePattern.MatchString(installed) || getenv("ORDINARY_GO_WORKER_MODE") != operation || getenv("ORDINARY_OWNERSHIP_SOURCE_REVISION") != installed {
		return c, ErrStartup
	}
	for _, key := range []string{"ORDINARY_OWNERSHIP_ROUTING_EPOCH", "ORDINARY_OWNERSHIP_PLAN_SHA256", "ORDINARY_OWNERSHIP_PROJECTION_SHA1", "ORDINARY_GO_COHORT_FILE"} {
		if getenv(key) != "" {
			return c, ErrStartup
		}
	}
	if coldB0ReactivationOperation(operation) {
		return readColdB0ReactivationConfig(getenv, installed, operation)
	}
	for _, key := range []string{"ORDINARY_COLD_RETIREMENT_EPOCH", "ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256", "ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256"} {
		if getenv(key) != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	}
	c.database, c.redis, c.source, c.operation = getenv("LOCAL_DATABASE_URL"), getenv("REDIS_URL"), installed, operation
	raw := getenv("ORDINARY_COLD_ROUTING_EPOCH")
	n, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || n < 1 || n > 9999999999999 || strconv.FormatInt(n, 10) != raw || c.database == "" || c.redis == "" {
		return ColdAdminConfig{}, ErrStartup
	}
	c.epoch = n
	c.intentFile, c.intentSHA = getenv("ORDINARY_COLD_INTENT_FILE"), getenv("ORDINARY_COLD_INTENT_SHA256")
	c.targetFile, c.targetSHA = getenv("ORDINARY_COLD_B0_TARGET_FILE"), getenv("ORDINARY_COLD_B0_TARGET_SHA256")
	c.luaFile, c.planSHA = getenv("ORDINARY_COLD_B0_LUA_FILE"), getenv("ORDINARY_COLD_PLAN_SHA256")
	c.namespace, c.shard, c.cohort = getenv("ORDINARY_COLD_B0_NAMESPACE"), getenv("ORDINARY_COLD_B0_SHARD_ID"), getenv("ORDINARY_COLD_B0_COHORT")
	c.reversalFile, c.reversalSHA = getenv("ORDINARY_COLD_REVERSAL_FILE"), getenv("ORDINARY_COLD_REVERSAL_SHA256")
	c.restoreRequestFile, c.restoreRequestSHA, c.restorePlanSHA = getenv("ORDINARY_COLD_B0_RESTORE_REQUEST_FILE"), getenv("ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256"), getenv("ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256")
	c.forwardRequestFile, c.forwardRequestSHA, c.forwardPlanSHA = getenv("ORDINARY_COLD_B0_FORWARD_REQUEST_FILE"), getenv("ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256"), getenv("ORDINARY_COLD_B0_FORWARD_PLAN_SHA256")
	c.ordinaryRequestFile, c.ordinaryRequestSHA, c.ordinaryRestorationSHA = getenv("ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE"), getenv("ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256"), getenv("ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256")
	if !coldOrdinaryRestorationOperation(operation) && (c.ordinaryRequestFile != "" || c.ordinaryRequestSHA != "" || c.ordinaryRestorationSHA != "") {
		return ColdAdminConfig{}, ErrStartup
	}
	c.forwardReceiptSHA = getenv("ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256")
	if !coldForwardPublicationOperation(operation) && c.forwardReceiptSHA != "" {
		return ColdAdminConfig{}, ErrStartup
	}
	path := func(p string) bool { return filepath.IsAbs(p) && !strings.ContainsRune(p, 0) }
	if !coldReversalOperation(operation) && (c.reversalFile != "" || c.reversalSHA != "") {
		return ColdAdminConfig{}, ErrStartup
	}
	if !coldB0RestorationOperation(operation) && (c.restoreRequestFile != "" || c.restoreRequestSHA != "" || (!coldOrdinaryRestorationOperation(operation) && c.restorePlanSHA != "")) {
		return ColdAdminConfig{}, ErrStartup
	}
	if !coldB0ForwardOperation(operation) && (c.forwardRequestFile != "" || c.forwardRequestSHA != "" || c.forwardPlanSHA != "") {
		return ColdAdminConfig{}, ErrStartup
	}
	if operation == "cold-b0-target" {
		if !path(c.luaFile) || c.namespace == "" || c.shard == "" || c.cohort == "" || c.intentFile != "" || c.intentSHA != "" || c.targetFile != "" || c.targetSHA != "" || c.planSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
		return c, nil
	}
	if !path(c.intentFile) || !planPattern.MatchString(c.intentSHA) || c.namespace != "" || c.shard != "" || c.cohort != "" {
		return ColdAdminConfig{}, ErrStartup
	}
	if coldOrdinaryRestorationOperation(operation) {
		if !path(c.reversalFile) || !planPattern.MatchString(c.reversalSHA) || !planPattern.MatchString(c.planSHA) || c.epoch <= 1 || !path(c.ordinaryRequestFile) || !planPattern.MatchString(c.ordinaryRequestSHA) || !planPattern.MatchString(c.restorePlanSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if operation == "cold-ordinary-rollback-plan" {
			if c.ordinaryRestorationSHA != "" {
				return ColdAdminConfig{}, ErrStartup
			}
		} else if !planPattern.MatchString(c.ordinaryRestorationSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if operation == "cold-ordinary-rollback-inspect" {
			if c.luaFile != "" || c.targetFile != "" || c.targetSHA != "" {
				return ColdAdminConfig{}, ErrStartup
			}
		} else if !path(c.luaFile) || !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		return c, nil
	}
	if coldB0ForwardOperation(operation) {
		if coldForwardPublicationOperation(operation) && !planPattern.MatchString(c.forwardReceiptSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if c.epoch <= 1 || !path(c.forwardRequestFile) || !planPattern.MatchString(c.forwardRequestSHA) || !planPattern.MatchString(c.planSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if operation == "cold-b0-forward-plan" {
			if c.forwardPlanSHA != "" {
				return ColdAdminConfig{}, ErrStartup
			}
		} else if !planPattern.MatchString(c.forwardPlanSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if operation == "cold-b0-forward-inspect" {
			if c.luaFile != "" || c.targetFile != "" || c.targetSHA != "" {
				return ColdAdminConfig{}, ErrStartup
			}
		} else if !path(c.luaFile) || !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		return c, nil
	}
	if coldReversalOperation(operation) {
		if !path(c.reversalFile) || !planPattern.MatchString(c.reversalSHA) || !planPattern.MatchString(c.planSHA) {
			return ColdAdminConfig{}, ErrStartup
		}
		if coldB0RestorationOperation(operation) {
			if !path(c.restoreRequestFile) || !planPattern.MatchString(c.restoreRequestSHA) {
				return ColdAdminConfig{}, ErrStartup
			}
			if operation == "cold-b0-rollback-plan" {
				if c.restorePlanSHA != "" {
					return ColdAdminConfig{}, ErrStartup
				}
			} else if !planPattern.MatchString(c.restorePlanSHA) {
				return ColdAdminConfig{}, ErrStartup
			}
			if operation == "cold-b0-rollback-inspect" {
				if c.luaFile != "" || c.targetFile != "" || c.targetSHA != "" {
					return ColdAdminConfig{}, ErrStartup
				}
			} else if !path(c.luaFile) || !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) {
				return ColdAdminConfig{}, ErrStartup
			}
		} else if c.luaFile != "" || c.targetFile != "" || c.targetSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
		return c, nil
	}
	if operation == "cold-reserve" || operation == "cold-inspect" {
		if c.luaFile != "" || c.targetFile != "" || c.targetSHA != "" || c.planSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if !path(c.luaFile) || !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	if operation == "cold-begin" {
		if c.planSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if operation != "cold-reserve" && operation != "cold-inspect" && !planPattern.MatchString(c.planSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	return c, nil
}

// RunColdAdmin exposes only queue/database primitives. The ADR006 host wrapper
// must independently verify all-writer quiescence and release/rollback evidence,
// transfer real B0 tasks and persist output. This does not deploy or start owners.
func RunColdAdmin(ctx context.Context, c ColdAdminConfig) (*ColdAdminIdentity, error) {
	if ColdAdminOperation("--"+c.operation) != c.operation || c.operation == "" || !sourcePattern.MatchString(c.source) || c.database == "" || c.redis == "" || c.epoch < 1 || c.epoch > 9999999999999 {
		return nil, ErrStartup
	}
	if coldB0ReactivationOperation(c.operation) {
		return runColdB0ReactivationAdmin(ctx, c)
	}
	var spec queue.ColdTransitionSpec
	var reversal queue.ColdReversalSpec
	var forwardRequest queue.ColdB0ForwardRequest
	var restoreRequest queue.ColdB0RollbackRequest
	var ordinaryRequest queue.ColdOrdinaryRestorationRequest
	var target *queue.ColdB0Target
	var lua []byte
	var err error
	if c.operation != "cold-b0-forward-inspect" && c.operation != "cold-reserve" && c.operation != "cold-inspect" && (!coldReversalOperation(c.operation) || coldB0RestorationOperation(c.operation) && c.operation != "cold-b0-rollback-inspect" || coldOrdinaryRestorationOperation(c.operation) && c.operation != "cold-ordinary-rollback-inspect") {
		lua, err = readProtectedOwnershipFile(c.luaFile, 128<<10)
		if err != nil {
			return nil, ErrStartup
		}
	}
	if c.operation != "cold-b0-target" {
		body, err := readProtectedOwnershipFile(c.intentFile, 4096)
		if err != nil {
			return nil, ErrStartup
		}
		spec, err = queue.DecodeColdTransitionSpec(string(body), c.intentSHA)
		if err != nil || spec.SourceRevision != c.source || ((c.operation == "cold-begin" || c.operation == "cold-reserve" || c.operation == "cold-inspect") && spec.PreviousEpoch != c.epoch) {
			return nil, ErrStartup
		}
		if coldB0ForwardOperation(c.operation) {
			body, err := readProtectedOwnershipFile(c.forwardRequestFile, 4096)
			if err != nil {
				return nil, ErrStartup
			}
			forwardRequest, err = queue.DecodeColdB0ForwardRequest(string(body), c.forwardRequestSHA)
			if err != nil || forwardRequest.SourceRevision != c.source || forwardRequest.IntentSHA256 != c.intentSHA || forwardRequest.RoutingEpoch != c.epoch || forwardRequest.OrdinaryPlanSHA256 != c.planSHA {
				return nil, ErrStartup
			}
		}
		if coldReversalOperation(c.operation) {
			body, err := readProtectedOwnershipFile(c.reversalFile, 4096)
			if err != nil {
				return nil, ErrStartup
			}
			reversal, err = queue.DecodeColdReversalSpec(string(body), c.reversalSHA)
			if err != nil || reversal.SourceRevision != c.source || reversal.ForwardIntentSHA256 != c.intentSHA || reversal.SourceEpoch != c.epoch || reversal.SourcePlanSHA256 != c.planSHA || reversal.RollbackReleaseSHA256 != spec.RollbackReleaseSHA256 || reversal.RollbackOrdinaryPlanSHA256 != spec.PreviousOrdinaryPlanSHA256 || reversal.RollbackB0ReceiptSHA256 != spec.PreviousB0ReceiptSHA256 {
				return nil, ErrStartup
			}
			if coldOrdinaryRestorationOperation(c.operation) {
				body, err := readProtectedOwnershipFile(c.ordinaryRequestFile, 4096)
				if err != nil {
					return nil, ErrStartup
				}
				ordinaryRequest, err = queue.DecodeColdOrdinaryRestorationRequest(string(body), c.ordinaryRequestSHA)
				if err != nil || ordinaryRequest.SourceRevision != c.source || ordinaryRequest.ReversalSHA256 != c.reversalSHA || ordinaryRequest.RetirementEpoch <= reversal.SourceEpoch || ordinaryRequest.B0RestorationPlanSHA256 != c.restorePlanSHA {
					return nil, ErrStartup
				}
				if c.operation != "cold-ordinary-rollback-inspect" {
					body, err := readProtectedOwnershipFile(c.targetFile, 16384)
					if err != nil {
						return nil, ErrStartup
					}
					target, err = queue.DecodeColdB0Target(string(body), c.targetSHA, lua)
					if err != nil || target.SHA256() != spec.TargetB0ManifestSHA256 {
						return nil, ErrStartup
					}
				}
			}
			if coldB0RestorationOperation(c.operation) {
				body, err := readProtectedOwnershipFile(c.restoreRequestFile, 4096)
				if err != nil {
					return nil, ErrStartup
				}
				restoreRequest, err = queue.DecodeColdB0RollbackRequest(string(body), c.restoreRequestSHA)
				if err != nil || restoreRequest.SourceRevision != c.source || restoreRequest.ReversalSHA256 != c.reversalSHA || restoreRequest.RetirementEpoch <= reversal.SourceEpoch || (restoreRequest.B0SourceEpoch != reversal.SourceEpoch && (restoreRequest.B0SourceEpoch != spec.PreviousEpoch || (reversal.SourcePhase != "reserved" && reversal.SourcePhase != "publishing"))) {
					return nil, ErrStartup
				}
				if c.operation != "cold-b0-rollback-inspect" {
					body, err := readProtectedOwnershipFile(c.targetFile, 16384)
					if err != nil {
						return nil, ErrStartup
					}
					target, err = queue.DecodeColdB0Target(string(body), c.targetSHA, lua)
					if err != nil || target.SHA256() != spec.TargetB0ManifestSHA256 {
						return nil, ErrStartup
					}
				}
			}
		} else if c.operation != "cold-reserve" && c.operation != "cold-inspect" && c.operation != "cold-b0-forward-inspect" {
			body, err := readProtectedOwnershipFile(c.targetFile, 16384)
			if err != nil {
				return nil, ErrStartup
			}
			target, err = queue.DecodeColdB0Target(string(body), c.targetSHA, lua)
			if err != nil || spec.TargetB0ManifestSHA256 != target.SHA256() {
				return nil, ErrStartup
			}
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	client, err := queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
	if err != nil {
		return nil, ErrStartup
	}
	defer client.Close()
	config, err := pgxpool.ParseConfig(c.database)
	if err != nil {
		return nil, ErrStartup
	}
	config.MinConns, config.MaxConns = 0, 1
	config.MaxConnIdleTime = time.Minute
	config.ConnConfig.ConnectTimeout = 3 * time.Second
	config.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeDescribeExec
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:ordinary-cold-coordinator:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "10s"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "15s"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, ErrStartup
	}
	defer pool.Close()
	result := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: c.operation, SourceRevision: c.source, IntentSHA256: c.intentSHA, B0TargetSHA256: c.targetSHA}
	var plan *queue.OwnershipPlan
	// The immutable journal binds the explicit new epoch/plan BEFORE effects.
	// The primitive then re-attests that journal and allocator under its locks.
	if coldForwardPublicationOperation(c.operation) || c.operation == "cold-prepare" || c.operation == "cold-publish" || c.operation == "cold-activate" {
		var exact bool
		err = pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.crawler_ownership_transition
 WHERE intent_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND reserved_plan_sha256=$4)`, c.intentSHA, c.source, c.epoch, c.planSHA).Scan(&exact)
		if err != nil || !exact {
			return nil, ErrStartup
		}
	}
	switch c.operation {
	case "cold-ordinary-rollback-plan", "cold-ordinary-rollback-retain", "cold-ordinary-rollback-inspect":
		var restoration *queue.ColdOrdinaryRestorationPlan
		switch c.operation {
		case "cold-ordinary-rollback-plan":
			restoration, err = queue.BuildColdOrdinaryRestorationPlan(ctx, pool, client, ordinaryRequest, target)
		case "cold-ordinary-rollback-retain":
			restoration, err = queue.RetainColdOrdinaryRestorationPlan(ctx, pool, client, ordinaryRequest, target, c.ordinaryRestorationSHA)
		case "cold-ordinary-rollback-inspect":
			restoration, err = queue.InspectColdOrdinaryRestorationPlan(ctx, pool, c.ordinaryRestorationSHA, c.source)
		}
		if err == nil {
			if restoration == nil || restoration.Request() != ordinaryRequest {
				return nil, ErrStartup
			}
			result.OrdinaryRestorationPlanSHA256, result.OrdinaryRestorationPlan, result.OrdinaryRestorationMode = restoration.SHA256(), json.RawMessage(restoration.Payload()), restoration.Mode()
			result.ReversalSHA256, result.RetirementEpoch = c.reversalSHA, ordinaryRequest.RetirementEpoch
			result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
			result.B0RollbackPlanSHA256 = c.restorePlanSHA
		}
	case "cold-forward-prepare", "cold-forward-publish", "cold-forward-activate":
		application, observedErr := queue.InspectColdB0ForwardApplication(ctx, pool, c.forwardPlanSHA, c.source)
		if observedErr != nil || application.Plan().Request() != forwardRequest || application.Phase() != "redis-transferred" || application.ReceiptSHA256() != c.forwardReceiptSHA {
			return nil, ErrStartup
		}
		binding := queue.ColdB0ForwardCompletionBinding{PlanSHA256: c.forwardPlanSHA, ReceiptSHA256: c.forwardReceiptSHA}
		control := b0producer.NewClient()
		switch c.operation {
		case "cold-forward-prepare":
			err = queue.PrepareColdForwardOwnershipPublication(ctx, pool, client, control, c.intentSHA, c.source, target, binding)
		case "cold-forward-publish":
			plan, err = queue.PublishColdForwardOwnership(ctx, pool, client, control, c.intentSHA, c.source, target, binding)
		case "cold-forward-activate":
			plan, err = queue.ActivateColdForwardOwnership(ctx, pool, client, control, c.intentSHA, c.source, target, binding)
		}
		result.B0ForwardPlanSHA256, result.B0ForwardReceiptSHA256, result.B0ForwardPhase = c.forwardPlanSHA, c.forwardReceiptSHA, application.Phase()
		result.B0ForwardPlan = json.RawMessage(application.Plan().Payload())
		result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
	case "cold-b0-forward-plan", "cold-b0-forward-retain", "cold-b0-forward-apply", "cold-b0-forward-inspect":
		var forward *queue.ColdB0ForwardPlan
		var application *queue.ColdB0ForwardApplication
		switch c.operation {
		case "cold-b0-forward-plan":
			forward, err = queue.BuildColdB0ForwardPlan(ctx, pool, client, b0producer.NewClient(), forwardRequest, target)
		case "cold-b0-forward-retain":
			forward, err = queue.RetainColdB0ForwardPlan(ctx, pool, client, b0producer.NewClient(), forwardRequest, target, c.forwardPlanSHA)
		case "cold-b0-forward-apply":
			application, err = queue.InspectColdB0ForwardApplication(ctx, pool, c.forwardPlanSHA, c.source)
			if err == nil && application.Plan().Request() != forwardRequest {
				return nil, ErrStartup
			}
			if err == nil {
				application, err = queue.ApplyColdB0ForwardPlan(ctx, pool, client, b0producer.NewClient(), c.forwardPlanSHA, c.source, target)
			}
		case "cold-b0-forward-inspect":
			application, err = queue.InspectColdB0ForwardApplication(ctx, pool, c.forwardPlanSHA, c.source)
		}
		if err == nil {
			if application != nil {
				forward = application.Plan()
				result.B0ForwardPhase, result.B0ForwardReceiptSHA256 = application.Phase(), application.ReceiptSHA256()
			}
			if forward.Request() != forwardRequest {
				return nil, ErrStartup
			}
			result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
			result.B0ForwardPlanSHA256 = forward.SHA256()
			result.B0ForwardPlan = json.RawMessage(forward.Payload())
		}
	case "cold-b0-rollback-plan", "cold-b0-rollback-retain", "cold-b0-rollback-restore", "cold-b0-rollback-inspect":
		var rollback *queue.ColdB0RollbackPlan
		var retained *queue.ColdB0RestorationState
		switch c.operation {
		case "cold-b0-rollback-plan":
			rollback, err = queue.BuildColdB0RollbackPlan(ctx, pool, client, restoreRequest, target)
		case "cold-b0-rollback-retain":
			retained, err = queue.RetainColdB0RollbackPlan(ctx, pool, client, restoreRequest, target, c.restorePlanSHA)
		case "cold-b0-rollback-restore":
			// Retained bytes are immutable. Compare the protected request before
			// any Redis effect; a matching revision/digest alone is insufficient.
			retained, err = queue.InspectColdB0Restoration(ctx, pool, c.restorePlanSHA, c.source)
			if err == nil && retained.Plan().Request() != restoreRequest {
				return nil, ErrStartup
			}
			if err == nil {
				retained, err = queue.RestoreColdB0Rollback(ctx, pool, client, c.restorePlanSHA, c.source, target)
			}
		case "cold-b0-rollback-inspect":
			retained, err = queue.InspectColdB0Restoration(ctx, pool, c.restorePlanSHA, c.source)
		}
		if err == nil {
			if retained != nil {
				rollback = retained.Plan()
				result.B0RestorationPhase = retained.Phase()
			}
			if rollback == nil || rollback.Request() != restoreRequest {
				return nil, ErrStartup
			}
			result.B0RollbackPlanSHA256 = rollback.SHA256()
			if c.operation == "cold-b0-rollback-plan" {
				result.B0RollbackPlan = json.RawMessage(rollback.Payload())
			}
			result.ReversalSHA256, result.RetirementEpoch = c.reversalSHA, restoreRequest.RetirementEpoch
			result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
		}
	case "cold-b0-target":
		target, err = queue.CaptureColdB0Target(ctx, pool, client, c.epoch, c.namespace, c.shard, c.cohort, lua)
		if err == nil {
			result.B0TargetSHA256, result.Target = target.SHA256(), json.RawMessage(target.Payload())
			result.RoutingEpoch = c.epoch
		}
	case "cold-begin":
		result.IntentSHA256, err = queue.BeginColdOwnershipTransition(ctx, pool, client, spec)
	case "cold-reserve":
		plan, err = queue.ReserveColdOwnershipEpoch(ctx, pool, client, c.intentSHA, c.source)
	case "cold-inspect":
		// Retained history only: this read grants no allocator/queue authority,
		// and never adopts a high-water or an unrelated latest reservation.
		var body string
		var epoch *int64
		var digest *string
		err = pool.QueryRow(ctx, `SELECT payload,phase,routing_epoch,reserved_plan_sha256
 FROM public.crawler_ownership_transition WHERE intent_sha256=$1 AND source_revision=$2`, c.intentSHA, c.source).Scan(&body, &result.RetainedPhase, &epoch, &digest)
		if err == nil {
			retained, decodeErr := queue.DecodeColdTransitionSpec(body, c.intentSHA)
			if decodeErr != nil || retained != spec || (epoch == nil) != (digest == nil) {
				return nil, ErrStartup
			}
			switch result.RetainedPhase {
			case "pending":
				if epoch != nil {
					return nil, ErrStartup
				}
			case "reserved", "publishing", "published", "active", "reversing", "reversed", "superseded":
			default:
				return nil, ErrStartup
			}
			if epoch != nil {
				if *epoch <= spec.PreviousEpoch || *epoch > 9999999999999 || !planPattern.MatchString(*digest) {
					return nil, ErrStartup
				}
				result.RoutingEpoch, result.PlanSHA256 = *epoch, *digest
			}
		}
	case "cold-prepare":
		err = queue.PrepareColdOwnershipPublication(ctx, pool, client, c.intentSHA, c.source, target)
		result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
	case "cold-publish":
		plan, err = queue.PublishColdOwnership(ctx, pool, client, c.intentSHA, c.source, target)
	case "cold-activate":
		plan, err = queue.ActivateColdOwnership(ctx, pool, client, c.intentSHA, c.source, target)
	case "cold-reversal-begin":
		result.ReversalSHA256, err = queue.BeginColdOwnershipReversal(ctx, pool, reversal)
		result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
	case "cold-reversal-reserve", "cold-reversal-inspect":
		var retained *queue.ColdReversalState
		if c.operation == "cold-reversal-reserve" {
			retained, err = queue.ReserveColdReversalEpoch(ctx, pool, c.reversalSHA, c.source)
		} else {
			retained, err = queue.InspectColdReversal(ctx, pool, c.reversalSHA, c.source)
		}
		if err == nil {
			if retained.Spec() != reversal {
				return nil, ErrStartup
			}
			result.ReversalSHA256, result.ReversalPhase, result.RetirementEpoch = retained.SHA256(), retained.Phase(), retained.RetirementEpoch()
			result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
		}
	}
	if err != nil {
		return nil, ErrStartup
	}
	if plan != nil {
		if plan.SourceRevision() != c.source || (c.operation != "cold-reserve" && (plan.Epoch() != c.epoch || plan.SHA256() != c.planSHA)) {
			return nil, ErrStartup
		}
		result.RoutingEpoch, result.PlanSHA256, result.ProjectionSHA1, result.Members = plan.Epoch(), plan.SHA256(), plan.ProjectionSHA1(), plan.MemberCount()
	}
	return result, nil
}
