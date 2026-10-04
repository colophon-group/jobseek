package worker

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"regexp"
	"strconv"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgxpool"
)

var firstImage = regexp.MustCompile(`^ghcr\.io/[^/]+/jobseek-crawler@sha256:[0-9a-f]{64}$`)

// FirstOwnershipRequest is the protected host's retained pending decision.
// The supported wrapper owns the original flock, stopped/restart-disabled
// writers, pinned installed release and B0 receipt, and full-stack readiness.
// This command never starts containers, selects releases or changes B0 epochs.
type FirstOwnershipRequest struct {
	Version         string `json:"version"`
	Operation       string `json:"operation"`
	SourceRevision  string `json:"source_revision"`
	RoutingEpoch    int64  `json:"routing_epoch"`
	PlanSHA256      string `json:"plan_sha256"`
	ProjectionSHA1  string `json:"projection_sha1"`
	CrawlerImageRef string `json:"crawler_image_ref"`
	B0ReceiptSHA256 string `json:"b0_receipt_sha256"`
	B0Cohort        string `json:"b0_cohort"`
	Namespace       string `json:"namespace"`
	ShardID         string `json:"shard_id"`
	ColdHostSHA256  string `json:"cold_host_sha256"`
}

type FirstOwnershipAdminConfig struct {
	database, redis         string
	request                 FirstOwnershipRequest
	digest                  string
	adminSource, adminImage string
}

func ReadFirstOwnershipAdminConfig(getenv func(string) string, installed, operation string) (FirstOwnershipAdminConfig, error) {
	var c FirstOwnershipAdminConfig
	if getenv == nil || !sourcePattern.MatchString(installed) || (operation != "activate" && operation != "retire") || getenv("ORDINARY_GO_WORKER_MODE") != operation+"-first-ownership" {
		return c, ErrStartup
	}
	body, err := readProtectedOwnershipFile(getenv("ORDINARY_FIRST_OWNERSHIP_REQUEST_FILE"), 8192)
	if err != nil {
		return c, ErrStartup
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&c.request) != nil || decoder.Decode(new(any)) != io.EOF {
		return FirstOwnershipAdminConfig{}, ErrStartup
	}
	r := c.request
	ownerSource := installed
	c.adminSource, c.adminImage = getenv("ORDINARY_RETIRE_ADMIN_SOURCE_REVISION"), getenv("ORDINARY_RETIRE_ADMIN_IMAGE_REF")
	if c.adminSource != "" || c.adminImage != "" {
		// Explicit compatibility retirement keeps the running executable's
		// compiled identity separate from the retained outgoing owner. This
		// cannot activate another source or grant runtime write authority.
		if operation != "retire" || c.adminSource != installed || !firstImage.MatchString(c.adminImage) || c.adminImage == r.CrawlerImageRef || !sourcePattern.MatchString(r.SourceRevision) || r.SourceRevision == installed {
			return FirstOwnershipAdminConfig{}, ErrStartup
		}
		ownerSource = r.SourceRevision
	}
	canonical, err := json.Marshal(r)
	if err != nil || !bytes.Equal(bytes.TrimSuffix(body, []byte("\n")), canonical) || r.Version != "jobseek.ordinary.first-owner-request/v1" || r.Operation != operation || r.SourceRevision != ownerSource || r.RoutingEpoch < 1 || r.RoutingEpoch > 9999999999999 || !planPattern.MatchString(r.PlanSHA256) || !sourcePattern.MatchString(r.ProjectionSHA1) || !firstImage.MatchString(r.CrawlerImageRef) || !planPattern.MatchString(r.B0ReceiptSHA256) || !planPattern.MatchString(r.ColdHostSHA256) || r.Namespace != "production-b0" || r.ShardID != "lightpanda-b0" || (r.B0Cohort != "c1" && r.B0Cohort != "c2" && r.B0Cohort != "c3" && r.B0Cohort != "cdom") {
		return FirstOwnershipAdminConfig{}, ErrStartup
	}
	for key, expected := range map[string]string{
		"ORDINARY_OWNERSHIP_SOURCE_REVISION": ownerSource,
		"ORDINARY_OWNERSHIP_PLAN_SHA256":     r.PlanSHA256,
		"ORDINARY_OWNERSHIP_PROJECTION_SHA1": r.ProjectionSHA1,
		"ORDINARY_OWNERSHIP_ROUTING_EPOCH":   strconv.FormatInt(r.RoutingEpoch, 10),
		"CRAWLER_IMAGE_REF":                  r.CrawlerImageRef,
		"LIGHTPANDA_B0_ROUTING_EPOCH":        strconv.FormatInt(r.RoutingEpoch, 10),
		"LIGHTPANDA_B0_QUEUE_NAMESPACE":      r.Namespace,
		"LIGHTPANDA_B0_SHARD_ID":             r.ShardID,
		"LIGHTPANDA_B0_PRODUCER_COHORT":      r.B0Cohort,
	} {
		if getenv(key) != expected {
			return FirstOwnershipAdminConfig{}, ErrStartup
		}
	}
	c.database, c.redis = getenv("LOCAL_DATABASE_URL"), getenv("REDIS_URL")
	if c.database == "" || c.redis == "" {
		return FirstOwnershipAdminConfig{}, ErrStartup
	}
	sum := sha256.Sum256(body)
	c.digest = hex.EncodeToString(sum[:])
	return c, nil
}

func RunFirstOwnershipAdmin(ctx context.Context, c FirstOwnershipAdminConfig) (*queue.FirstOwnershipResult, error) {
	if c.database == "" || c.redis == "" || !planPattern.MatchString(c.digest) {
		return nil, ErrStartup
	}
	// Legacy cancellation retains ten-minute SQL leases after every process
	// stops. The protected host remains cold while they expire naturally.
	ctx, cancel := context.WithTimeout(ctx, 14*time.Minute)
	defer cancel()
	config, err := pgxpool.ParseConfig(c.database)
	if err != nil {
		return nil, ErrStartup
	}
	config.MaxConns, config.MinConns = 2, 0
	config.ConnConfig.ConnectTimeout = 3 * time.Second
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:first-ordinary-owner:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "10000"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, ErrStartup
	}
	defer pool.Close()
	client, err := queue.Open(c.redis, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
	if err != nil {
		return nil, ErrStartup
	}
	defer client.Close()
	if err := waitFirstOwnershipLegacyLeases(ctx, pool); err != nil {
		return nil, ErrStartup
	}
	r := c.request
	var result *queue.FirstOwnershipResult
	err = queue.WithHostColdSQL(ctx, pool, queue.HostColdSQLBinding{SourceRevision: r.SourceRevision, RequestSHA256: c.digest, ContainmentIntentSHA256: r.ColdHostSHA256}, func(ctx context.Context, _ *queue.HostColdSQL) error {
		// Validate the legacy/native startup projection identity before any
		// Redis or SQL ownership effect; the queue API validates the full plan.
		var payload string
		if err := pool.QueryRow(ctx, "SELECT payload FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3", r.PlanSHA256, r.SourceRevision, r.RoutingEpoch).Scan(&payload); err != nil {
			return ErrStartup
		}
		projection, err := queue.OwnershipProjectionSHA1(payload, r.PlanSHA256)
		if err != nil || projection != r.ProjectionSHA1 {
			return ErrStartup
		}
		target, err := queue.CaptureFirstOwnershipB0(ctx, pool, client, r.RoutingEpoch, r.Namespace, r.ShardID, r.B0Cohort)
		if err != nil {
			return err
		}
		if r.Operation == "activate" {
			result, err = queue.ActivateFirstOwnershipInHostScope(ctx, pool, client, r.RoutingEpoch, r.PlanSHA256, r.SourceRevision, target)
		} else {
			result, err = queue.RetireFirstOwnershipInHostScope(ctx, pool, client, r.RoutingEpoch, r.PlanSHA256, r.SourceRevision, target)
		}
		if err != nil || result.ProjectionSHA1 != r.ProjectionSHA1 {
			return ErrStartup
		}
		return nil
	})
	if err != nil {
		return nil, ErrStartup
	}
	result.AdminSourceRevision, result.AdminImageRef = c.adminSource, c.adminImage
	return result, nil
}

// Observe database time without clearing leases or inventing completions. The
// existing exclusive SQL scope rechecks this predicate after acquiring all
// barriers, so a fresh lease or nonparticipating writer still refuses cutover.
func waitFirstOwnershipLegacyLeases(ctx context.Context, pool *pgxpool.Pool) error {
	ctx, cancel := context.WithTimeout(ctx, 11*time.Minute)
	defer cancel()
	for {
		var clear bool
		if err := pool.QueryRow(ctx, `SELECT
 NOT EXISTS(SELECT 1 FROM public.job_board WHERE leased_until>clock_timestamp())
 AND NOT EXISTS(SELECT 1 FROM public.job_posting WHERE leased_until>clock_timestamp())`).Scan(&clear); err != nil {
			return ErrStartup
		}
		if clear {
			return nil
		}
		if !waitRuntime(ctx, time.Second) {
			return ErrStartup
		}
	}
}
