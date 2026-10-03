package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed cold_b0_reactivation.sql
var coldB0ReactivationSchema string

type ColdB0ReactivationRequest struct {
	OrdinaryRestorationPlanSHA256 string `json:"ordinary_restoration_plan_sha256"`
	SourceRevision                string `json:"source_revision"`
}

func (r ColdB0ReactivationRequest) valid() bool {
	return ownershipSHA256.MatchString(r.OrdinaryRestorationPlanSHA256) && ownershipRevision.MatchString(r.SourceRevision)
}

type coldB0ReactivationDocument struct {
	Version               string                    `json:"version"`
	Request               ColdB0ReactivationRequest `json:"request"`
	RetirementEpoch       int64                     `json:"retirement_epoch"`
	RollbackReceipt       string                    `json:"rollback_receipt"`
	RollbackReceiptSHA256 string                    `json:"rollback_receipt_sha256"`
	PreviousProjection    string                    `json:"previous_projection"`
	PreviousMarker        string                    `json:"previous_marker"`
	ForwardPlanSHA256     string                    `json:"forward_plan_sha256"`
	Forward               coldB0ForwardDocument     `json:"forward"`
}

type ColdB0ReactivationPlan struct {
	document     coldB0ReactivationDocument
	body, digest string
	forward      *ColdB0ForwardPlan
}

func (p *ColdB0ReactivationPlan) SHA256() string                     { return p.digest }
func (p *ColdB0ReactivationPlan) Payload() string                    { return p.body }
func (p *ColdB0ReactivationPlan) Request() ColdB0ReactivationRequest { return p.document.Request }
func (p *ColdB0ReactivationPlan) RetirementEpoch() int64             { return p.document.RetirementEpoch }
func (p *ColdB0ReactivationPlan) RollbackReceiptSHA256() string {
	return p.document.RollbackReceiptSHA256
}

var coldB0ReceiptImage = regexp.MustCompile(`^ghcr\.io/[a-z0-9][a-z0-9-]*/jobseek-crawler@sha256:[0-9a-f]{64}$`)

// Parse the exact existing host receipt format as data. Its hash binds historical
// release identity; independently verified images/specs and sentinel clearing
// remain host responsibilities. No receipt field is executed or adopted as env.
func coldB0ReactivationReceipt(body, digest string, target *ColdB0Target, epoch int64) (map[string]string, error) {
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 4096 || hex.EncodeToString(h[:]) != digest || !strings.HasSuffix(body, "\n") || strings.ContainsAny(body, "\r\x00") {
		return nil, ErrAuthorityLost
	}
	keys := []string{"schema", "state", "cohort", "namespace", "shard_id", "routing_epoch", "plan_digest", "compose_digest", "crawler_image_ref", "deploy_revision", "activated_at_epoch"}
	lines := strings.Split(strings.TrimSuffix(body, "\n"), "\n")
	if len(lines) != len(keys) {
		return nil, ErrAuthorityLost
	}
	values := map[string]string{}
	for _, line := range lines {
		if strings.Count(line, "=") != 1 {
			return nil, ErrAuthorityLost
		}
		pair := strings.SplitN(line, "=", 2)
		if _, exists := values[pair[0]]; exists {
			return nil, ErrAuthorityLost
		}
		for _, r := range line {
			if r < 32 || r > 126 {
				return nil, ErrAuthorityLost
			}
		}
		values[pair[0]] = pair[1]
	}
	for _, key := range keys {
		if _, ok := values[key]; !ok {
			return nil, ErrAuthorityLost
		}
	}
	old, err := strconv.ParseInt(values["routing_epoch"], 10, 64)
	activated, other := strconv.ParseInt(values["activated_at_epoch"], 10, 64)
	if err != nil || other != nil || old < 1 || old >= epoch || strconv.FormatInt(old, 10) != values["routing_epoch"] || activated < 1 || strconv.FormatInt(activated, 10) != values["activated_at_epoch"] || values["schema"] != "jobseek.lightpanda-b0-active/v1" || values["state"] != "active" || !ownershipSHA256.MatchString(values["plan_digest"]) || !ownershipSHA256.MatchString(values["compose_digest"]) || !ownershipRevision.MatchString(values["deploy_revision"]) || !coldB0ReceiptImage.MatchString(values["crawler_image_ref"]) || target == nil || values["cohort"] != target.document.Cohort || values["namespace"] != target.document.Namespace || values["shard_id"] != target.document.ShardID {
		return nil, ErrAuthorityLost
	}
	return values, nil
}

func decodeColdB0ReactivationPlan(body, digest string) (*ColdB0ReactivationPlan, error) {
	h := sha256.Sum256([]byte(body))
	var doc coldB0ReactivationDocument
	if len(body) < 1 || len(body) > 64*1024*1024 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return nil, ErrAuthorityLost
	}
	decoder := json.NewDecoder(strings.NewReader(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&doc) != nil || decoder.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-b0-reactivation/v1" || !doc.Request.valid() || doc.RetirementEpoch < 2 || doc.RetirementEpoch > 9999999999999 || !ownershipSHA256.MatchString(doc.RollbackReceiptSHA256) || len(doc.PreviousProjection) > 16*1024*1024 || len(doc.PreviousMarker) > 4096 {
		return nil, ErrAuthorityLost
	}
	forwardBody, err := json.Marshal(doc.Forward)
	if err != nil {
		return nil, ErrAuthorityLost
	}
	forward, err := decodeColdB0ForwardPlan(string(forwardBody), coldB0ForwardHash(doc.Forward))
	if err != nil {
		return nil, err
	}
	if forward.digest != doc.ForwardPlanSHA256 || forward.Request().SourceRevision != doc.Request.SourceRevision || forward.Request().OrdinaryPlanSHA256 != doc.Request.OrdinaryRestorationPlanSHA256 || forward.Request().RoutingEpoch != doc.RetirementEpoch {
		return nil, ErrAuthorityLost
	}
	if _, err := coldB0ReactivationReceipt(doc.RollbackReceipt, doc.RollbackReceiptSHA256, &ColdB0Target{document: doc.Forward.Target}, doc.RetirementEpoch); err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return &ColdB0ReactivationPlan{doc, body, digest, forward}, nil
}

func coldB0ReactivationContext(ctx context.Context, tx pgx.Tx, c *Client, r ColdB0ReactivationRequest, target *ColdB0Target, receipt string) (*ColdOrdinaryRestorationPlan, *ColdReversalState, error) {
	ordinary, err := loadColdOrdinaryRestoration(ctx, tx, r.OrdinaryRestorationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, err
	}
	request := ordinary.Request()
	reversal, err := loadColdReversal(ctx, tx, request.ReversalSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, err
	}
	b0, err := loadColdB0Restoration(ctx, tx, request.B0RestorationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, err
	}
	if reversal.phase != "reserved" || reversal.retirement != request.RetirementEpoch || b0.phase != "fences-cleared" || b0.plan.Request().ReversalSHA256 != request.ReversalSHA256 || b0.plan.Request().RetirementEpoch != request.RetirementEpoch || reversal.spec.RollbackB0ReceiptSHA256 == "" {
		return nil, nil, ErrAuthorityLost
	}
	originalTargetBody := ""
	if err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_target WHERE target_sha256=$1", b0.plan.document.TargetSHA256).Scan(&originalTargetBody); err != nil {
		return nil, nil, err
	}
	originalTarget, err := DecodeColdB0Target(originalTargetBody, b0.plan.document.TargetSHA256, []byte(target.lua))
	if err != nil {
		return nil, nil, err
	}
	if err := requireColdB0RollbackContext(ctx, tx, b0.plan.Request(), originalTarget); err != nil {
		return nil, nil, err
	}
	values, err := coldB0ReactivationReceipt(receipt, reversal.spec.RollbackB0ReceiptSHA256, target, request.RetirementEpoch)
	if err != nil {
		return nil, nil, err
	}
	var previous int64
	if err := tx.QueryRow(ctx, "SELECT previous_epoch FROM crawler_ownership_transition WHERE intent_sha256=$1", reversal.spec.ForwardIntentSHA256).Scan(&previous); err != nil {
		return nil, nil, err
	}
	if values["routing_epoch"] != strconv.FormatInt(previous, 10) || ordinary.fresh != nil && values["deploy_revision"] != ordinary.fresh.SourceRevision() {
		return nil, nil, ErrAuthorityLost
	}
	plan := &OwnershipPlan{document: ownershipDocument{Epoch: request.RetirementEpoch}}
	if ordinary.fresh != nil {
		plan = ordinary.fresh
		var staged string
		if err := tx.QueryRow(ctx, "SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND state='staged' AND source_revision=$2 AND routing_epoch=$3", plan.digest, plan.SourceRevision(), plan.Epoch()).Scan(&staged); err != nil || staged != plan.body {
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return nil, nil, err
			}
			return nil, nil, ErrAuthorityLost
		}
		a := &Authority{queue: c}
		ids := []string{}
		for _, member := range plan.document.Members {
			profile, _, err := a.observeGreenhouseMonitor(ctx, tx, member.BoardID)
			if err != nil {
				return nil, nil, err
			}
			if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
				return nil, nil, ErrAuthorityLost
			}
			ids = append(ids, member.BoardID)
		}
		var leased bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM job_board WHERE id=ANY($1::uuid[]) AND leased_until>now()) OR EXISTS(SELECT 1 FROM job_posting WHERE board_id=ANY($1::uuid[]) AND leased_until>now())`, ids).Scan(&leased); err != nil {
			return nil, nil, err
		}
		if leased {
			return nil, nil, ErrAuthorityLost
		}
	}
	if err := target.attest(ctx, tx, c, plan); err != nil {
		return nil, nil, err
	}
	return ordinary, reversal, nil
}

func coldB0ReactivationPrior(ctx context.Context, tx pgx.Tx, c *Client, state *ColdReversalState) (string, string, error) {
	// Loss of an earlier witness may cause the cold reversal. Retain that exact
	// absence; only known historical bytes are otherwise admissible. The new
	// generation will later use a distinct, completed publication identity.
	values := []string{}
	for _, key := range []string{ownershipProjectionKey, coldPublicationKey} {
		kind, err := c.redis.Type(ctx, key).Result()
		if err != nil {
			return "", "", ErrObservation
		}
		if kind == "none" {
			values = append(values, "")
			continue
		}
		if kind != "string" {
			return "", "", ErrAuthorityLost
		}
		ttl, err := c.redis.PTTL(ctx, key).Result()
		if err != nil {
			return "", "", ErrObservation
		}
		if ttl != -1 {
			return "", "", ErrAuthorityLost
		}
		value, err := c.redis.Get(ctx, key).Result()
		if err != nil {
			return "", "", ErrObservation
		}
		values = append(values, value)
	}
	var sourceBody string
	if err := tx.QueryRow(ctx, "SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1", state.spec.SourcePlanSHA256).Scan(&sourceBody); err != nil {
		return "", "", err
	}
	source, err := decodeOwnership(sourceBody, state.spec.SourcePlanSHA256)
	if err != nil {
		return "", "", err
	}
	var originalBody string
	if err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_transition WHERE intent_sha256=$1", state.spec.ForwardIntentSHA256).Scan(&originalBody); err != nil {
		return "", "", err
	}
	spec, err := decodeColdTransition(originalBody, state.spec.ForwardIntentSHA256)
	if err != nil {
		return "", "", err
	}
	projections := map[string]bool{"": true, sourceBody: true}
	markers := map[string]bool{"": true, publicationMarker(state.spec.ForwardIntentSHA256, spec, source, "pending"): true, publicationMarker(state.spec.ForwardIntentSHA256, spec, source, "published"): true}
	if state.spec.RollbackOrdinaryPlanSHA256 != "" {
		var oldBody, oldRevision string
		var oldEpoch int64
		if err := tx.QueryRow(ctx, "SELECT payload,source_revision,routing_epoch FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND state='retired'", state.spec.RollbackOrdinaryPlanSHA256).Scan(&oldBody, &oldRevision, &oldEpoch); err != nil {
			return "", "", err
		}
		projections[oldBody] = true
		var priorBody, priorIntent string
		err := tx.QueryRow(ctx, "SELECT payload,intent_sha256 FROM crawler_ownership_transition WHERE reserved_plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND phase='superseded'", state.spec.RollbackOrdinaryPlanSHA256, oldRevision, oldEpoch).Scan(&priorBody, &priorIntent)
		if err == nil {
			old, decodeErr := decodeOwnership(oldBody, state.spec.RollbackOrdinaryPlanSHA256)
			if decodeErr != nil {
				return "", "", decodeErr
			}
			prior, decodeErr := decodeColdTransition(priorBody, priorIntent)
			if decodeErr != nil {
				return "", "", decodeErr
			}
			markers[publicationMarker(priorIntent, prior, old, "published")] = true
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return "", "", err
		}
	}
	if !projections[values[0]] || !markers[values[1]] {
		return "", "", ErrAuthorityLost
	}
	return values[0], values[1], nil
}

func reactivationPublication(ordinary *ColdOrdinaryRestorationPlan, previous, marker string) *coldPublication {
	plan := &OwnershipPlan{document: ownershipDocument{Epoch: ordinary.Request().RetirementEpoch}}
	if ordinary.fresh != nil {
		plan = ordinary.fresh
	}
	return &coldPublication{phase: "reserved", plan: plan, previous: previous, previousMarker: marker}
}

func deriveColdB0Reactivation(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, r ColdB0ReactivationRequest, target *ColdB0Target, receipt string) (*ColdB0ReactivationPlan, error) {
	ordinary, state, err := coldB0ReactivationContext(ctx, tx, c, r, target, receipt)
	if err != nil {
		return nil, err
	}
	previous, marker, err := coldB0ReactivationPrior(ctx, tx, c, state)
	if err != nil {
		return nil, err
	}
	request := ColdB0ForwardRequest{IntentSHA256: state.spec.ForwardIntentSHA256, SourceRevision: r.SourceRevision, RoutingEpoch: ordinary.Request().RetirementEpoch, OrdinaryPlanSHA256: r.OrdinaryRestorationPlanSHA256}
	forward, err := deriveObservedColdB0ForwardPlan(ctx, tx, c, control, request, target, reactivationPublication(ordinary, previous, marker))
	if err != nil {
		return nil, err
	}
	if _, _, err := coldB0ReactivationContext(ctx, tx, c, r, target, receipt); err != nil {
		return nil, err
	}
	doc := coldB0ReactivationDocument{"jobseek.crawler.cold-b0-reactivation/v1", r, ordinary.Request().RetirementEpoch, receipt, state.spec.RollbackB0ReceiptSHA256, previous, marker, forward.digest, forward.document}
	body, err := json.Marshal(doc)
	if err != nil || len(body) > 64*1024*1024 {
		return nil, ErrProtocol
	}
	return decodeColdB0ReactivationPlan(string(body), coldB0ForwardHash(doc))
}

func BuildColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdB0ReactivationRequest, target *ColdB0Target, receipt string) (*ColdB0ReactivationPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() {
		return nil, ErrConfiguration
	}
	return buildColdB0ReactivationPlan(ctx, pool, c, control, r, target, receipt)
}
func buildColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, r ColdB0ReactivationRequest, target *ColdB0Target, receipt string) (*ColdB0ReactivationPlan, error) {
	var result *ColdB0ReactivationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = deriveColdB0Reactivation(ctx, tx, c, control, r, target, receipt)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func loadColdB0ReactivationPlan(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdB0ReactivationPlan, error) {
	var body string
	err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_reactivation WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	plan, err := decodeColdB0ReactivationPlan(body, digest)
	if err != nil || plan.Request().SourceRevision != revision {
		return nil, ErrAuthorityLost
	}
	return plan, nil
}

func RetainColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdB0ReactivationRequest, target *ColdB0Target, receipt, approved string) (*ColdB0ReactivationPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() || !ownershipSHA256.MatchString(approved) {
		return nil, ErrConfiguration
	}
	return retainColdB0ReactivationPlan(ctx, pool, c, control, r, target, receipt, approved)
}
func retainColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, r ColdB0ReactivationRequest, target *ColdB0Target, receipt, approved string) (*ColdB0ReactivationPlan, error) {
	var result *ColdB0ReactivationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		plan, err := deriveColdB0Reactivation(ctx, tx, c, control, r, target, receipt)
		if err != nil {
			return err
		}
		if plan.digest != approved {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, "INSERT INTO crawler_ownership_b0_target(target_sha256,payload) VALUES($1,$2) ON CONFLICT(target_sha256) DO NOTHING", target.digest, target.body); err != nil {
			return err
		}
		var body string
		if err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_b0_target WHERE target_sha256=$1", target.digest).Scan(&body); err != nil {
			return err
		}
		if body != target.body {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, `INSERT INTO crawler_ownership_b0_reactivation(plan_sha256,ordinary_restoration_plan_sha256,source_revision,retirement_epoch,target_sha256,forward_plan_sha256,payload) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(ordinary_restoration_plan_sha256) DO NOTHING`, plan.digest, r.OrdinaryRestorationPlanSHA256, r.SourceRevision, plan.document.RetirementEpoch, target.digest, plan.forward.digest, plan.body); err != nil {
			return err
		}
		result, err = loadColdB0ReactivationPlan(ctx, tx, approved, r.SourceRevision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func inspectColdB0Reactivation(ctx context.Context, pool *pgxpool.Pool, digest, revision string, fn func(context.Context, pgx.Tx) error) error {
	if ctx == nil || pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		return fn(ctx, tx)
	})
}
func InspectColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdB0ReactivationPlan, error) {
	var result *ColdB0ReactivationPlan
	err := inspectColdB0Reactivation(ctx, pool, digest, revision, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = loadColdB0ReactivationPlan(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// Retained application uses the same exact transfer classifier as forward
// cutover. Its original reversal remains open; no ordinary owner is published.
func applyColdB0Reactivation(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardActivationControl, digest, revision string, target *ColdB0Target) (*ColdB0ReactivationApplication, error) {
	var result *ColdB0ReactivationApplication
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdB0ReactivationApplication(ctx, tx, digest, revision)
		if err != nil {
			return err
		}
		plan := state.plan
		if plan.document.Forward.TargetSHA256 != target.digest {
			return ErrAuthorityLost
		}
		ordinary, _, err := coldB0ReactivationContext(ctx, tx, c, plan.Request(), target, plan.document.RollbackReceipt)
		if err != nil {
			return err
		}
		publication := reactivationPublication(ordinary, plan.document.PreviousProjection, plan.document.PreviousMarker)
		observe := func() (*coldB0ForwardSnapshot, string, error) {
			if _, _, err := coldB0ReactivationContext(ctx, tx, c, plan.Request(), target, plan.document.RollbackReceipt); err != nil {
				return nil, "", err
			}
			rows, hash, err := coldB0ForwardRows(ctx, tx, target)
			if err != nil {
				return nil, "", err
			}
			if hash != plan.forward.document.PostgresSHA256 || !reflect.DeepEqual(rows, plan.forward.document.PostgresRows) {
				return nil, "", ErrAuthorityLost
			}
			snapshot, hash, err := observeColdB0Forward(ctx, c, target, publication, rows)
			if err != nil {
				return nil, "", err
			}
			if err := attestColdForwardManifest(ctx, control, target, snapshot); err != nil {
				return nil, "", err
			}
			return snapshot, hash, nil
		}
		if state.application != nil {
			_, hash, err := observe()
			if err != nil {
				return err
			}
			if hash != state.application.receipt.SnapshotSHA256 {
				return ErrAuthorityLost
			}
			result = state
			return nil
		}
		snapshot, err := applyObservedColdB0Forward(ctx, tx, c, control, plan.forward, target, publication)
		if err != nil {
			return err
		}
		hash := coldB0ForwardHash(snapshot)
		if reply, err := c.redis.Save(ctx).Result(); err != nil || reply != "OK" {
			return ErrObservation
		}
		_, readback, err := observe()
		if err != nil {
			return err
		}
		if hash != readback {
			return ErrAuthorityLost
		}
		receipt := coldB0ForwardReceipt{"jobseek.crawler.cold-b0-forward-application/v1", plan.forward.digest, plan.forward.Request(), target.digest, hash, snapshot, plan.forward.document.ProjectedOccupancy}
		doc := coldB0ReactivationReceiptDocument{"jobseek.crawler.cold-b0-reactivation-application/v1", plan.digest, receipt}
		body, err := json.Marshal(doc)
		if err != nil || len(body) > 32*1024*1024 {
			return ErrProtocol
		}
		receiptSHA := coldB0ForwardHash(doc)
		if _, err := decodeColdB0ReactivationApplication(plan, string(body), receiptSHA); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "INSERT INTO crawler_ownership_b0_reactivation_completion(plan_sha256,source_revision,receipt_sha256,payload) VALUES($1,$2,$3,$4)", digest, revision, receiptSHA, string(body)); err != nil {
			return err
		}
		result, err = loadColdB0ReactivationApplication(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
func ApplyColdB0ReactivationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, digest, revision string, target *ColdB0Target) (*ColdB0ReactivationApplication, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	return applyColdB0Reactivation(ctx, pool, c, control, digest, revision, target)
}
