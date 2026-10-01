package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
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

func coldB0ReactivationOperation(operation string) bool {
	return operation == "cold-b0-reactivation-plan" || operation == "cold-b0-reactivation-retain" || operation == "cold-b0-reactivation-apply" || operation == "cold-b0-reactivation-inspect"
}

func readColdB0ReactivationConfig(getenv func(string) string, source, operation string) (ColdAdminConfig, error) {
	c := ColdAdminConfig{source: source, operation: operation, database: getenv("LOCAL_DATABASE_URL"), redis: getenv("REDIS_URL")}
	// Keep original source E distinct from already reserved retirement R. Neither
	// an allocator high-water nor a receipt can implicitly select an epoch.
	epoch := func(key string) (int64, bool) {
		raw := getenv(key)
		n, err := strconv.ParseInt(raw, 10, 64)
		return n, err == nil && n > 0 && n <= 9999999999999 && strconv.FormatInt(n, 10) == raw
	}
	var valid bool
	c.epoch, valid = epoch("ORDINARY_COLD_ROUTING_EPOCH")
	if !valid {
		return ColdAdminConfig{}, ErrStartup
	}
	c.retirementEpoch, valid = epoch("ORDINARY_COLD_RETIREMENT_EPOCH")
	if !valid || c.retirementEpoch <= c.epoch || c.database == "" || c.redis == "" {
		return ColdAdminConfig{}, ErrStartup
	}
	c.intentFile, c.intentSHA = getenv("ORDINARY_COLD_INTENT_FILE"), getenv("ORDINARY_COLD_INTENT_SHA256")
	c.reversalFile, c.reversalSHA = getenv("ORDINARY_COLD_REVERSAL_FILE"), getenv("ORDINARY_COLD_REVERSAL_SHA256")
	c.planSHA, c.ordinaryRestorationSHA = getenv("ORDINARY_COLD_PLAN_SHA256"), getenv("ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256")
	c.targetFile, c.targetSHA, c.luaFile = getenv("ORDINARY_COLD_B0_TARGET_FILE"), getenv("ORDINARY_COLD_B0_TARGET_SHA256"), getenv("ORDINARY_COLD_B0_LUA_FILE")
	c.priorB0ReceiptFile, c.priorB0ReceiptSHA = getenv("ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE"), getenv("ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256")
	c.reactivationPlanSHA = getenv("ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256")
	path := func(p string) bool { return filepath.IsAbs(p) && !strings.ContainsRune(p, 0) }
	if !path(c.intentFile) || !path(c.reversalFile) || !planPattern.MatchString(c.intentSHA) || !planPattern.MatchString(c.reversalSHA) || !planPattern.MatchString(c.planSHA) || !planPattern.MatchString(c.ordinaryRestorationSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	for _, key := range []string{
		"ORDINARY_COLD_B0_NAMESPACE", "ORDINARY_COLD_B0_SHARD_ID", "ORDINARY_COLD_B0_COHORT",
		"ORDINARY_COLD_B0_RESTORE_REQUEST_FILE", "ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256", "ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256",
		"ORDINARY_COLD_B0_FORWARD_REQUEST_FILE", "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256", "ORDINARY_COLD_B0_FORWARD_PLAN_SHA256", "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256",
		"ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256",
	} {
		if getenv(key) != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	}
	if operation == "cold-b0-reactivation-plan" {
		if c.reactivationPlanSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if !planPattern.MatchString(c.reactivationPlanSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	if operation == "cold-b0-reactivation-inspect" {
		if c.targetFile != "" || c.targetSHA != "" || c.luaFile != "" || c.priorB0ReceiptFile != "" || c.priorB0ReceiptSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) || !path(c.luaFile) || !path(c.priorB0ReceiptFile) || !planPattern.MatchString(c.priorB0ReceiptSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	return c, nil
}

func runColdB0ReactivationAdmin(ctx context.Context, c ColdAdminConfig) (*ColdAdminIdentity, error) {
	if ctx == nil || c.retirementEpoch <= c.epoch || c.retirementEpoch > 9999999999999 {
		return nil, ErrStartup
	}
	body, err := readProtectedOwnershipFile(c.intentFile, 4096)
	if err != nil {
		return nil, ErrStartup
	}
	spec, err := queue.DecodeColdTransitionSpec(string(body), c.intentSHA)
	if err != nil || spec.SourceRevision != c.source {
		return nil, ErrStartup
	}
	body, err = readProtectedOwnershipFile(c.reversalFile, 4096)
	if err != nil {
		return nil, ErrStartup
	}
	reversal, err := queue.DecodeColdReversalSpec(string(body), c.reversalSHA)
	if err != nil || reversal.SourceRevision != c.source || reversal.ForwardIntentSHA256 != c.intentSHA || reversal.SourceEpoch != c.epoch || reversal.SourcePlanSHA256 != c.planSHA || reversal.RollbackReleaseSHA256 != spec.RollbackReleaseSHA256 || reversal.RollbackOrdinaryPlanSHA256 != spec.PreviousOrdinaryPlanSHA256 || reversal.RollbackB0ReceiptSHA256 != spec.PreviousB0ReceiptSHA256 || reversal.RollbackB0ReceiptSHA256 == "" {
		return nil, ErrStartup
	}
	var target *queue.ColdB0Target
	receipt := ""
	if c.operation != "cold-b0-reactivation-inspect" {
		lua, err := readProtectedOwnershipFile(c.luaFile, 128<<10)
		if err != nil {
			return nil, ErrStartup
		}
		body, err = readProtectedOwnershipFile(c.targetFile, 16384)
		if err != nil {
			return nil, ErrStartup
		}
		target, err = queue.DecodeColdB0Target(string(body), c.targetSHA, lua)
		if err != nil {
			return nil, ErrStartup
		}
		body, err = readProtectedOwnershipFile(c.priorB0ReceiptFile, 4096)
		if err != nil {
			return nil, ErrStartup
		}
		hash := sha256.Sum256(body)
		if hex.EncodeToString(hash[:]) != c.priorB0ReceiptSHA || c.priorB0ReceiptSHA != reversal.RollbackB0ReceiptSHA256 {
			return nil, ErrStartup
		}
		receipt = string(body)
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	config, err := pgxpool.ParseConfig(c.database)
	if err != nil {
		return nil, ErrStartup
	}
	config.MinConns, config.MaxConns = 0, 1
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
	ordinary, err := queue.InspectColdOrdinaryRestorationPlan(ctx, pool, c.ordinaryRestorationSHA, c.source)
	if err != nil || ordinary.Request().ReversalSHA256 != c.reversalSHA || ordinary.Request().RetirementEpoch != c.retirementEpoch {
		return nil, ErrStartup
	}
	r := queue.ColdB0ReactivationRequest{OrdinaryRestorationPlanSHA256: c.ordinaryRestorationSHA, SourceRevision: c.source}
	var plan *queue.ColdB0ReactivationPlan
	var application *queue.ColdB0ReactivationApplication
	if c.operation == "cold-b0-reactivation-inspect" {
		application, err = queue.InspectColdB0ReactivationApplication(ctx, pool, c.reactivationPlanSHA, c.source)
	} else {
		client, openErr := queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
		if openErr != nil {
			return nil, ErrStartup
		}
		defer client.Close()
		control := b0producer.NewClient()
		switch c.operation {
		case "cold-b0-reactivation-plan":
			plan, err = queue.BuildColdB0ReactivationPlan(ctx, pool, client, control, r, target, receipt)
		case "cold-b0-reactivation-retain":
			plan, err = queue.RetainColdB0ReactivationPlan(ctx, pool, client, control, r, target, receipt, c.reactivationPlanSHA)
		case "cold-b0-reactivation-apply":
			application, err = queue.InspectColdB0ReactivationApplication(ctx, pool, c.reactivationPlanSHA, c.source)
			if err == nil && (application.Plan().Request() != r || application.Plan().RollbackReceiptSHA256() != c.priorB0ReceiptSHA) {
				return nil, ErrStartup
			}
			if err == nil {
				application, err = queue.ApplyColdB0ReactivationPlan(ctx, pool, client, control, c.reactivationPlanSHA, c.source, target)
			}
		default:
			return nil, ErrStartup
		}
	}
	if err != nil {
		return nil, ErrStartup
	}
	result := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: c.operation, SourceRevision: c.source, IntentSHA256: c.intentSHA, RoutingEpoch: c.epoch, PlanSHA256: c.planSHA, RetirementEpoch: c.retirementEpoch, ReversalSHA256: c.reversalSHA, OrdinaryRestorationPlanSHA256: c.ordinaryRestorationSHA, B0TargetSHA256: c.targetSHA}
	if application != nil {
		plan = application.Plan()
		result.B0ReactivationPhase, result.B0ReactivationReceiptSHA256 = application.Phase(), application.ReceiptSHA256()
	}
	if plan == nil || plan.Request() != r || plan.RetirementEpoch() != c.retirementEpoch {
		return nil, ErrStartup
	}
	result.B0ReactivationPlanSHA256, result.B0ReactivationPlan = plan.SHA256(), json.RawMessage(plan.Payload())
	return result, nil
}
