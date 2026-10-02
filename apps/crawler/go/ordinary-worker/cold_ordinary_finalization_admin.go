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
)

func coldOrdinaryFinalizationOperation(operation string) bool {
	switch operation {
	case "cold-ordinary-finalization-plan", "cold-ordinary-finalization-retain", "cold-ordinary-finalization-prepare", "cold-ordinary-finalization-publish", "cold-ordinary-finalization-complete", "cold-ordinary-finalization-inspect":
		return true
	}
	return false
}

func readColdOrdinaryFinalizationConfig(getenv func(string) string, source, operation string) (ColdAdminConfig, error) {
	c := ColdAdminConfig{source: source, operation: operation, database: getenv("LOCAL_DATABASE_URL"), redis: getenv("REDIS_URL")}
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
	if !valid || c.retirementEpoch <= c.epoch || c.database == "" || (c.redis == "" && operation != "cold-ordinary-finalization-inspect") {
		return ColdAdminConfig{}, ErrStartup
	}
	c.intentFile, c.intentSHA = getenv("ORDINARY_COLD_INTENT_FILE"), getenv("ORDINARY_COLD_INTENT_SHA256")
	c.reversalFile, c.reversalSHA = getenv("ORDINARY_COLD_REVERSAL_FILE"), getenv("ORDINARY_COLD_REVERSAL_SHA256")
	c.planSHA, c.ordinaryRestorationSHA = getenv("ORDINARY_COLD_PLAN_SHA256"), getenv("ORDINARY_COLD_ORDINARY_RESTORATION_PLAN_SHA256")
	c.reactivationPlanSHA = getenv("ORDINARY_COLD_B0_REACTIVATION_PLAN_SHA256")
	c.finalizationRequestFile, c.finalizationRequestSHA = getenv("ORDINARY_COLD_FINALIZATION_REQUEST_FILE"), getenv("ORDINARY_COLD_FINALIZATION_REQUEST_SHA256")
	c.finalizationPlanSHA = getenv("ORDINARY_COLD_FINALIZATION_PLAN_SHA256")
	c.targetFile, c.targetSHA, c.luaFile = getenv("ORDINARY_COLD_B0_TARGET_FILE"), getenv("ORDINARY_COLD_B0_TARGET_SHA256"), getenv("ORDINARY_COLD_B0_LUA_FILE")
	path := func(p string) bool { return filepath.IsAbs(p) && !strings.ContainsRune(p, 0) }
	if !path(c.intentFile) || !path(c.reversalFile) || !path(c.finalizationRequestFile) || !planPattern.MatchString(c.intentSHA) || !planPattern.MatchString(c.reversalSHA) || !planPattern.MatchString(c.planSHA) || !planPattern.MatchString(c.ordinaryRestorationSHA) || !planPattern.MatchString(c.reactivationPlanSHA) || !planPattern.MatchString(c.finalizationRequestSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	for _, key := range []string{
		"ORDINARY_COLD_B0_NAMESPACE", "ORDINARY_COLD_B0_SHARD_ID", "ORDINARY_COLD_B0_COHORT",
		"ORDINARY_COLD_B0_RESTORE_REQUEST_FILE", "ORDINARY_COLD_B0_RESTORE_REQUEST_SHA256", "ORDINARY_COLD_B0_RESTORATION_PLAN_SHA256",
		"ORDINARY_COLD_B0_FORWARD_REQUEST_FILE", "ORDINARY_COLD_B0_FORWARD_REQUEST_SHA256", "ORDINARY_COLD_B0_FORWARD_PLAN_SHA256", "ORDINARY_COLD_B0_FORWARD_RECEIPT_SHA256",
		"ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_FILE", "ORDINARY_COLD_ORDINARY_RESTORE_REQUEST_SHA256",
		"ORDINARY_COLD_PRIOR_B0_RECEIPT_FILE", "ORDINARY_COLD_PRIOR_B0_RECEIPT_SHA256",
	} {
		if getenv(key) != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	}
	if operation == "cold-ordinary-finalization-plan" {
		if c.finalizationPlanSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if !planPattern.MatchString(c.finalizationPlanSHA) {
		return ColdAdminConfig{}, ErrStartup
	}
	if operation == "cold-ordinary-finalization-inspect" {
		if c.targetFile != "" || c.targetSHA != "" || c.luaFile != "" {
			return ColdAdminConfig{}, ErrStartup
		}
	} else if !path(c.targetFile) || !planPattern.MatchString(c.targetSHA) || !path(c.luaFile) {
		return ColdAdminConfig{}, ErrStartup
	}
	return c, nil
}

func runColdOrdinaryFinalizationAdmin(ctx context.Context, c ColdAdminConfig, borrowed *coldAdminConnections) (*ColdAdminIdentity, error) {
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
	if err != nil || reversal.SourceRevision != c.source || reversal.ForwardIntentSHA256 != c.intentSHA || reversal.SourceEpoch != c.epoch || reversal.SourcePlanSHA256 != c.planSHA || reversal.RollbackReleaseSHA256 != spec.RollbackReleaseSHA256 || reversal.RollbackOrdinaryPlanSHA256 != spec.PreviousOrdinaryPlanSHA256 || reversal.RollbackB0ReceiptSHA256 != spec.PreviousB0ReceiptSHA256 {
		return nil, ErrStartup
	}
	body, err = readProtectedOwnershipFile(c.finalizationRequestFile, 4096)
	if err != nil {
		return nil, ErrStartup
	}
	r, err := queue.DecodeColdOrdinaryFinalizationRequest(string(body), c.finalizationRequestSHA)
	if err != nil || r.SourceRevision != c.source || r.OrdinaryRestorationPlanSHA256 != c.ordinaryRestorationSHA || r.B0ReactivationPlanSHA256 != c.reactivationPlanSHA {
		return nil, ErrStartup
	}
	var target *queue.ColdB0Target
	if c.operation != "cold-ordinary-finalization-inspect" {
		lua, err := readProtectedOwnershipFile(c.luaFile, 128<<10)
		if err != nil {
			return nil, ErrStartup
		}
		body, err := readProtectedOwnershipFile(c.targetFile, 16384)
		if err != nil {
			return nil, ErrStartup
		}
		target, err = queue.DecodeColdB0Target(string(body), c.targetSHA, lua)
		if err != nil {
			return nil, ErrStartup
		}
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	connections, close, err := acquireColdAdminConnections(ctx, c, borrowed, c.operation != "cold-ordinary-finalization-inspect")
	if err != nil {
		return nil, ErrStartup
	}
	defer close()
	pool, client := connections.pool, connections.client
	ordinary, err := queue.InspectColdOrdinaryRestorationPlan(ctx, pool, c.ordinaryRestorationSHA, c.source)
	if err != nil || ordinary.Request().ReversalSHA256 != c.reversalSHA || ordinary.Request().RetirementEpoch != c.retirementEpoch {
		return nil, ErrStartup
	}
	b0, err := queue.InspectColdB0ReactivationApplication(ctx, pool, c.reactivationPlanSHA, c.source)
	if err != nil || b0.Plan().Request().OrdinaryRestorationPlanSHA256 != c.ordinaryRestorationSHA || b0.Plan().RetirementEpoch() != c.retirementEpoch || b0.Phase() != "redis-reactivated" || !planPattern.MatchString(b0.ReceiptSHA256()) {
		return nil, ErrStartup
	}
	var plan *queue.ColdOrdinaryFinalizationPlan
	var application *queue.ColdOrdinaryFinalizationApplication
	if c.operation == "cold-ordinary-finalization-inspect" {
		application, err = queue.InspectColdOrdinaryFinalizationApplication(ctx, pool, c.finalizationPlanSHA, c.source)
	} else {
		// Bind protected request to retained approval BEFORE a producer/Redis effect.
		if c.operation != "cold-ordinary-finalization-plan" && c.operation != "cold-ordinary-finalization-retain" {
			application, err = queue.InspectColdOrdinaryFinalizationApplication(ctx, pool, c.finalizationPlanSHA, c.source)
			if err != nil || application.Plan().Request() != r || application.Plan().RetirementEpoch() != c.retirementEpoch {
				return nil, ErrStartup
			}
		}
		control := b0producer.NewClient()
		switch c.operation {
		case "cold-ordinary-finalization-plan":
			plan, err = queue.BuildColdOrdinaryFinalizationPlan(ctx, pool, client, control, r, target)
		case "cold-ordinary-finalization-retain":
			plan, err = queue.RetainColdOrdinaryFinalizationPlan(ctx, pool, client, control, r, target, c.finalizationPlanSHA)
		case "cold-ordinary-finalization-prepare":
			err = queue.PrepareColdOrdinaryFinalization(ctx, pool, client, control, c.finalizationPlanSHA, c.source, target)
			if err == nil {
				application, err = queue.InspectColdOrdinaryFinalizationApplication(ctx, pool, c.finalizationPlanSHA, c.source)
			}
		case "cold-ordinary-finalization-publish":
			application, err = queue.PublishColdOrdinaryFinalization(ctx, pool, client, control, c.finalizationPlanSHA, c.source, target)
		case "cold-ordinary-finalization-complete":
			application, err = queue.CompleteColdOrdinaryFinalization(ctx, pool, client, control, c.finalizationPlanSHA, c.source, target)
		default:
			return nil, ErrStartup
		}
	}
	if err != nil {
		return nil, ErrStartup
	}
	result := &ColdAdminIdentity{Version: "jobseek.ordinary.cold-identity/v1", Operation: c.operation, SourceRevision: c.source, IntentSHA256: c.intentSHA, RoutingEpoch: c.epoch, PlanSHA256: c.planSHA, RetirementEpoch: c.retirementEpoch, ReversalSHA256: c.reversalSHA, OrdinaryRestorationPlanSHA256: c.ordinaryRestorationSHA, B0ReactivationPlanSHA256: c.reactivationPlanSHA, B0ReactivationReceiptSHA256: b0.ReceiptSHA256(), OrdinaryRestorationMode: ordinary.Mode(), B0TargetSHA256: c.targetSHA}
	if application != nil {
		plan = application.Plan()
		result.OrdinaryFinalizationPhase = application.Phase()
		result.OrdinaryPublicationReceiptSHA256 = application.PublicationReceiptSHA256()
		result.OrdinaryFinalizationReceiptSHA256 = application.ReceiptSHA256()
		if application.ReceiptPayload() != "" {
			result.OrdinaryFinalizationReceipt = json.RawMessage(application.ReceiptPayload())
		}
	}
	if plan == nil || plan.Request() != r || plan.RetirementEpoch() != c.retirementEpoch || plan.Mode() != ordinary.Mode() {
		return nil, ErrStartup
	}
	result.OrdinaryFinalizationPlanSHA256, result.OrdinaryFinalizationPlan = plan.SHA256(), json.RawMessage(plan.Payload())
	if fresh := ordinary.FreshOwnershipPlan(); fresh != nil {
		result.RestoredOrdinaryPlanSHA256, result.RestoredOrdinaryProjectionSHA1, result.RestoredOrdinarySourceRevision = fresh.SHA256(), fresh.ProjectionSHA1(), fresh.SourceRevision()
	}
	return result, nil
}
