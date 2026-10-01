package worker

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ColdAdminOperation recognizes only explicit one-shot coordinator arguments.
func ColdAdminOperation(argument string) string {
	switch argument {
	case "--cold-b0-target", "--cold-begin", "--cold-reserve", "--cold-prepare", "--cold-publish", "--cold-activate":
		return strings.TrimPrefix(argument, "--")
	}
	return ""
}

type ColdAdminConfig struct {
	database, redis, source, operation                    string
	intentFile, intentSHA, targetFile, targetSHA, luaFile string
	planSHA, namespace, shard, cohort                     string
	epoch                                                 int64
}

// ColdAdminIdentity reports the completed primitive, not host release readiness
// or a grant to start services. Target contains only bounded board identities and
// configuration digests; the host must durably retain its exact canonical bytes.
type ColdAdminIdentity struct {
	Version        string          `json:"version"`
	Operation      string          `json:"operation"`
	SourceRevision string          `json:"source_revision"`
	IntentSHA256   string          `json:"intent_sha256,omitempty"`
	RoutingEpoch   int64           `json:"routing_epoch,omitempty"`
	PlanSHA256     string          `json:"plan_sha256,omitempty"`
	ProjectionSHA1 string          `json:"projection_sha1,omitempty"`
	Members        int             `json:"members,omitempty"`
	B0TargetSHA256 string          `json:"b0_target_sha256,omitempty"`
	Target         json.RawMessage `json:"target,omitempty"`
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
	path := func(p string) bool { return filepath.IsAbs(p) && !strings.ContainsRune(p, 0) }
	if operation == "cold-b0-target" {
		if !path(c.luaFile) || c.namespace == "" || c.shard == "" || c.cohort == "" || c.intentFile != "" || c.intentSHA != "" || c.targetFile != "" || c.targetSHA != "" || c.planSHA != "" {
			return ColdAdminConfig{}, ErrStartup
		}
		return c, nil
	}
	if !path(c.intentFile) || !planPattern.MatchString(c.intentSHA) || c.namespace != "" || c.shard != "" || c.cohort != "" {
		return ColdAdminConfig{}, ErrStartup
	}
	if operation == "cold-reserve" {
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
	} else if operation != "cold-reserve" && !planPattern.MatchString(c.planSHA) {
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
	var spec queue.ColdTransitionSpec
	var target *queue.ColdB0Target
	var lua []byte
	var err error
	if c.operation != "cold-reserve" {
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
		if err != nil || spec.SourceRevision != c.source || ((c.operation == "cold-begin" || c.operation == "cold-reserve") && spec.PreviousEpoch != c.epoch) {
			return nil, ErrStartup
		}
		if c.operation != "cold-reserve" {
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
	if c.operation == "cold-prepare" || c.operation == "cold-publish" || c.operation == "cold-activate" {
		var exact bool
		err = pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.crawler_ownership_transition
 WHERE intent_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND reserved_plan_sha256=$4)`, c.intentSHA, c.source, c.epoch, c.planSHA).Scan(&exact)
		if err != nil || !exact {
			return nil, ErrStartup
		}
	}
	switch c.operation {
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
	case "cold-prepare":
		err = queue.PrepareColdOwnershipPublication(ctx, pool, client, c.intentSHA, c.source, target)
		result.RoutingEpoch, result.PlanSHA256 = c.epoch, c.planSHA
	case "cold-publish":
		plan, err = queue.PublishColdOwnership(ctx, pool, client, c.intentSHA, c.source, target)
	case "cold-activate":
		plan, err = queue.ActivateColdOwnership(ctx, pool, client, c.intentSHA, c.source, target)
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
