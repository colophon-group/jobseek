package queue

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"strings"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed cold_ordinary_finalization.sql
var coldOrdinaryFinalizationSchema string

//go:embed cold_ordinary_finalization.lua
var coldOrdinaryFinalizationLua string

// Host evidence identities are data. The ADR006 coordinator independently
// verifies its mutation lock, all-writer quiescence and immutable releases.
type ColdOrdinaryFinalizationRequest struct {
	OrdinaryRestorationPlanSHA256 string `json:"ordinary_restoration_plan_sha256"`
	B0ReactivationPlanSHA256      string `json:"b0_reactivation_plan_sha256"`
	SourceRevision                string `json:"source_revision"`
	TransitionID                  string `json:"transition_id"`
	ActiveReleaseSHA256           string `json:"active_release_sha256"`
	ColdAttestationSHA256         string `json:"cold_attestation_sha256"`
}

func (r ColdOrdinaryFinalizationRequest) valid() bool {
	return ownershipSHA256.MatchString(r.OrdinaryRestorationPlanSHA256) && ownershipSHA256.MatchString(r.B0ReactivationPlanSHA256) && ownershipRevision.MatchString(r.SourceRevision) && canonicalUUID.MatchString(r.TransitionID) && ownershipSHA256.MatchString(r.ActiveReleaseSHA256) && ownershipSHA256.MatchString(r.ColdAttestationSHA256)
}

// DecodeColdOrdinaryFinalizationRequest binds a protected closed canonical request.
func DecodeColdOrdinaryFinalizationRequest(body, digest string) (ColdOrdinaryFinalizationRequest, error) {
	var r ColdOrdinaryFinalizationRequest
	if len(body) < 1 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || coldForwardBytesDigest(body) != digest {
		return r, ErrAuthorityLost
	}
	d := json.NewDecoder(strings.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || d.Decode(new(any)) != io.EOF || !r.valid() {
		return ColdOrdinaryFinalizationRequest{}, ErrAuthorityLost
	}
	canonical, err := json.Marshal(r)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return ColdOrdinaryFinalizationRequest{}, ErrAuthorityLost
	}
	return r, nil
}

type coldOrdinaryFinalizationDocument struct {
	Version                 string                          `json:"version"`
	Request                 ColdOrdinaryFinalizationRequest `json:"request"`
	ReversalSHA256          string                          `json:"reversal_sha256"`
	RetirementEpoch         int64                           `json:"retirement_epoch"`
	Mode                    string                          `json:"mode"`
	B0ReceiptSHA256         string                          `json:"b0_receipt_sha256"`
	TargetSHA256            string                          `json:"target_sha256"`
	FreshOrdinaryPlanSHA256 string                          `json:"fresh_ordinary_plan_sha256"`
	CompatibleIntentSHA256  string                          `json:"compatible_intent_sha256"`
	CompatiblePayload       string                          `json:"compatible_payload"`
	PreviousProjection      string                          `json:"previous_projection"`
	PreviousMarker          string                          `json:"previous_marker"`
}
type ColdOrdinaryFinalizationPlan struct {
	document     coldOrdinaryFinalizationDocument
	body, digest string
	compatible   *ColdTransitionSpec
}

func (p *ColdOrdinaryFinalizationPlan) SHA256() string  { return p.digest }
func (p *ColdOrdinaryFinalizationPlan) Payload() string { return p.body }
func (p *ColdOrdinaryFinalizationPlan) Request() ColdOrdinaryFinalizationRequest {
	return p.document.Request
}
func (p *ColdOrdinaryFinalizationPlan) RetirementEpoch() int64 { return p.document.RetirementEpoch }
func (p *ColdOrdinaryFinalizationPlan) Mode() string           { return p.document.Mode }

func decodeColdOrdinaryFinalization(body, digest string) (*ColdOrdinaryFinalizationPlan, error) {
	if len(body) < 1 || len(body) > 32*1024*1024 || !ownershipSHA256.MatchString(digest) || coldForwardBytesDigest(body) != digest {
		return nil, ErrAuthorityLost
	}
	var doc coldOrdinaryFinalizationDocument
	d := json.NewDecoder(strings.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&doc) != nil || d.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-ordinary-finalization/v1" || !doc.Request.valid() || !ownershipSHA256.MatchString(doc.ReversalSHA256) || !ownershipSHA256.MatchString(doc.B0ReceiptSHA256) || !ownershipSHA256.MatchString(doc.TargetSHA256) || doc.RetirementEpoch < 2 || doc.RetirementEpoch > 9999999999999 || len(doc.PreviousProjection) > 16*1024*1024 || len(doc.PreviousMarker) > 4096 {
		return nil, ErrAuthorityLost
	}
	p := &ColdOrdinaryFinalizationPlan{document: doc, body: body, digest: digest}
	switch doc.Mode {
	case "native":
		spec, err := decodeColdTransition(doc.CompatiblePayload, doc.CompatibleIntentSHA256)
		if err != nil || !ownershipSHA256.MatchString(doc.FreshOrdinaryPlanSHA256) || spec.TransitionID != doc.Request.TransitionID || spec.PreviousEpoch >= doc.RetirementEpoch || spec.TargetB0ManifestSHA256 != doc.TargetSHA256 || spec.ActiveReleaseSHA256 != doc.Request.ActiveReleaseSHA256 || spec.ColdAttestationSHA256 != doc.Request.ColdAttestationSHA256 || spec.PreviousOrdinaryPlanSHA256 != spec.PreparedPlanSHA256 {
			return nil, ErrAuthorityLost
		}
		p.compatible = &spec
	case "legacy":
		if doc.FreshOrdinaryPlanSHA256 != "" || doc.CompatibleIntentSHA256 != "" || doc.CompatiblePayload != "" {
			return nil, ErrAuthorityLost
		}
	default:
		return nil, ErrAuthorityLost
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return p, nil
}
func loadColdOrdinaryFinalization(ctx context.Context, tx pgx.Tx, digest, source string) (*ColdOrdinaryFinalizationPlan, error) {
	var body string
	err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_restoration_finalization WHERE plan_sha256=$1 AND source_revision=$2", digest, source).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	p, err := decodeColdOrdinaryFinalization(body, digest)
	if err != nil || p.Request().SourceRevision != source {
		return nil, ErrAuthorityLost
	}
	return p, nil
}

// This context observes the immutable completed B0 application without replaying
// an activation. It remains valid while the ordinary publication bytes change.
func finalizationContext(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, r ColdOrdinaryFinalizationRequest, target *ColdB0Target, complete bool, approved *ColdOrdinaryFinalizationPlan, publicationPhase string) (*ColdOrdinaryRestorationPlan, *ColdReversalState, *ColdB0ReactivationApplication, error) {
	ordinary, err := loadColdOrdinaryRestoration(ctx, tx, r.OrdinaryRestorationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, nil, err
	}
	b0, err := loadColdB0ReactivationApplication(ctx, tx, r.B0ReactivationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, nil, err
	}
	if b0.application == nil || b0.plan.Request().OrdinaryRestorationPlanSHA256 != ordinary.digest || b0.plan.document.RetirementEpoch != ordinary.Request().RetirementEpoch || b0.plan.document.Forward.TargetSHA256 != target.digest {
		return nil, nil, nil, ErrAuthorityLost
	}
	state, err := loadColdReversal(ctx, tx, ordinary.Request().ReversalSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, nil, err
	}
	phase, forward := "reserved", "reversing"
	if complete {
		phase, forward = "complete", "reversed"
	}
	if state.phase != phase || state.retirement != ordinary.Request().RetirementEpoch || state.spec.RollbackB0ReceiptSHA256 != b0.plan.document.RollbackReceiptSHA256 {
		return nil, nil, nil, ErrAuthorityLost
	}
	if err := reversalForwardBinding(ctx, tx, state.spec, forward); err != nil {
		return nil, nil, nil, err
	}
	restored, err := loadColdB0Restoration(ctx, tx, ordinary.Request().B0RestorationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, nil, nil, err
	}
	if restored.phase != "fences-cleared" || restored.plan.Request().ReversalSHA256 != state.digest || restored.plan.Request().RetirementEpoch != state.retirement {
		return nil, nil, nil, ErrAuthorityLost
	}
	var current int64
	var called bool
	if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
		return nil, nil, nil, err
	}
	if !called || current != state.retirement {
		return nil, nil, nil, ErrAuthorityLost
	}
	var active string
	err = tx.QueryRow(ctx, "SELECT plan_sha256 FROM ordinary_worker_ownership_plan WHERE state='active'").Scan(&active)
	if complete && ordinary.fresh != nil {
		if err != nil || active != ordinary.fresh.digest {
			return nil, nil, nil, ErrAuthorityLost
		}
	} else if !errors.Is(err, pgx.ErrNoRows) {
		if err != nil {
			return nil, nil, nil, err
		}
		return nil, nil, nil, ErrAuthorityLost
	}
	owner := &OwnershipPlan{document: ownershipDocument{Epoch: state.retirement}}
	if ordinary.fresh != nil {
		owner = ordinary.fresh
		expected := "staged"
		if complete {
			expected = "active"
		}
		var body string
		if err := tx.QueryRow(ctx, "SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND state=$2", owner.digest, expected).Scan(&body); err != nil || body != owner.body {
			return nil, nil, nil, ErrAuthorityLost
		}
		a := &Authority{queue: c}
		ids := []string{}
		for _, m := range owner.document.Members {
			profile, _, err := a.observeGreenhouseMonitor(ctx, tx, m.BoardID)
			if err != nil {
				return nil, nil, nil, err
			}
			if profile.CompanyID != m.CompanyID || profile.Domain != m.Domain || profile.EffectiveConfigSHA256 != m.EffectiveConfigHash {
				return nil, nil, nil, ErrAuthorityLost
			}
			ids = append(ids, m.BoardID)
		}
		var leased bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM job_board WHERE id=ANY($1::uuid[]) AND leased_until>now()) OR EXISTS(SELECT 1 FROM job_posting WHERE board_id=ANY($1::uuid[]) AND leased_until>now())`, ids).Scan(&leased); err != nil {
			return nil, nil, nil, err
		}
		if leased {
			return nil, nil, nil, ErrAuthorityLost
		}
	}
	if err := target.attest(ctx, tx, c, owner); err != nil {
		return nil, nil, nil, err
	}
	rows, hash, err := coldB0ForwardRows(ctx, tx, target)
	if err != nil {
		return nil, nil, nil, err
	}
	if hash != b0.plan.forward.document.PostgresSHA256 || !reflect.DeepEqual(rows, b0.plan.forward.document.PostgresRows) {
		return nil, nil, nil, ErrAuthorityLost
	}
	// Projection/witness are classified separately before the single-command B0
	// observation. The snapshot excludes those two publication fields.
	previous, marker, err := finalizationReadPair(ctx, c)
	if err != nil {
		return nil, nil, nil, err
	}
	if approved == nil {
		if previous != b0.plan.document.PreviousProjection || marker != b0.plan.document.PreviousMarker {
			return nil, nil, nil, ErrAuthorityLost
		}
	} else if !finalizationPairAllowed(approved, ordinary, publicationPhase, previous, marker) {
		return nil, nil, nil, ErrAuthorityLost
	}
	snapshot, hash, err := observeColdB0Forward(ctx, c, target, reactivationPublication(ordinary, previous, marker), rows)
	if err != nil {
		return nil, nil, nil, err
	}
	if hash != b0.application.receipt.SnapshotSHA256 {
		return nil, nil, nil, ErrAuthorityLost
	}
	if err := attestColdForwardManifest(ctx, control, target, snapshot); err != nil {
		return nil, nil, nil, err
	}
	return ordinary, state, b0, nil
}
func finalizationReadPair(ctx context.Context, c *Client) (string, string, error) {
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
		if value == "" {
			return "", "", ErrAuthorityLost
		}
		values = append(values, value)
	}
	return values[0], values[1], nil
}
func deriveColdOrdinaryFinalization(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, r ColdOrdinaryFinalizationRequest, target *ColdB0Target) (*ColdOrdinaryFinalizationPlan, error) {
	ordinary, state, b0, err := finalizationContext(ctx, tx, c, control, r, target, false, nil, "prepared")
	if err != nil {
		return nil, err
	}
	previous, marker, err := coldB0ReactivationPrior(ctx, tx, c, state)
	if err != nil {
		return nil, err
	}
	if previous != b0.plan.document.PreviousProjection || marker != b0.plan.document.PreviousMarker {
		return nil, ErrAuthorityLost
	}
	var originalBody string
	if err := tx.QueryRow(ctx, "SELECT payload FROM crawler_ownership_transition WHERE intent_sha256=$1", state.spec.ForwardIntentSHA256).Scan(&originalBody); err != nil {
		return nil, err
	}
	original, err := decodeColdTransition(originalBody, state.spec.ForwardIntentSHA256)
	if err != nil {
		return nil, err
	}
	if r.ActiveReleaseSHA256 != original.ActiveReleaseSHA256 && r.ActiveReleaseSHA256 != original.TargetReleaseSHA256 {
		return nil, ErrAuthorityLost
	}
	doc := coldOrdinaryFinalizationDocument{Version: "jobseek.crawler.cold-ordinary-finalization/v1", Request: r, ReversalSHA256: state.digest, RetirementEpoch: state.retirement, Mode: ordinary.Mode(), B0ReceiptSHA256: b0.digest, TargetSHA256: target.digest, PreviousProjection: previous, PreviousMarker: marker}
	if ordinary.fresh != nil {
		spec := ColdTransitionSpec{Version: coldTransitionVersion, TransitionID: r.TransitionID, SourceRevision: ordinary.fresh.SourceRevision(), PreviousEpoch: ordinary.document.PreviousOwner.Epoch, PreviousOrdinaryPlanSHA256: ordinary.document.PreviousOrdinaryPlanSHA256, PreparedPlanSHA256: ordinary.document.PreviousOrdinaryPlanSHA256, PreviousB0ReceiptSHA256: state.spec.RollbackB0ReceiptSHA256, TargetB0ManifestSHA256: target.digest, ActiveReleaseSHA256: r.ActiveReleaseSHA256, TargetReleaseSHA256: state.spec.RollbackReleaseSHA256, RollbackReleaseSHA256: state.spec.RollbackReleaseSHA256, ColdAttestationSHA256: r.ColdAttestationSHA256}
		body, err := json.Marshal(spec)
		if err != nil {
			return nil, ErrProtocol
		}
		doc.CompatiblePayload = string(body)
		doc.CompatibleIntentSHA256 = coldForwardBytesDigest(string(body))
		doc.FreshOrdinaryPlanSHA256 = ordinary.fresh.digest
	}
	body, err := json.Marshal(doc)
	if err != nil {
		return nil, ErrProtocol
	}
	return decodeColdOrdinaryFinalization(string(body), coldForwardBytesDigest(string(body)))
}
func BuildColdOrdinaryFinalizationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdOrdinaryFinalizationRequest, target *ColdB0Target) (*ColdOrdinaryFinalizationPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() {
		return nil, ErrConfiguration
	}
	return buildColdOrdinaryFinalization(ctx, pool, c, control, r, target)
}
func buildColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, r ColdOrdinaryFinalizationRequest, target *ColdB0Target) (*ColdOrdinaryFinalizationPlan, error) {
	var result *ColdOrdinaryFinalizationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = deriveColdOrdinaryFinalization(ctx, tx, c, control, r, target)
		return err
	})
	return result, err
}
func RetainColdOrdinaryFinalizationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, r ColdOrdinaryFinalizationRequest, target *ColdB0Target, approved string) (*ColdOrdinaryFinalizationPlan, error) {
	if ctx == nil || c == nil || control == nil || target == nil || !r.valid() || !ownershipSHA256.MatchString(approved) {
		return nil, ErrConfiguration
	}
	return retainColdOrdinaryFinalization(ctx, pool, c, control, r, target, approved)
}
func retainColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, r ColdOrdinaryFinalizationRequest, target *ColdB0Target, approved string) (*ColdOrdinaryFinalizationPlan, error) {
	var result *ColdOrdinaryFinalizationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		p, err := deriveColdOrdinaryFinalization(ctx, tx, c, control, r, target)
		if err != nil {
			return err
		}
		if p.digest != approved {
			return ErrAuthorityLost
		}
		var fresh, compatible any
		if p.compatible != nil {
			fresh, compatible = p.document.FreshOrdinaryPlanSHA256, p.document.CompatibleIntentSHA256
		}
		_, err = tx.Exec(ctx, `INSERT INTO crawler_ownership_restoration_finalization(plan_sha256,ordinary_restoration_plan_sha256,b0_reactivation_plan_sha256,b0_receipt_sha256,source_revision,retirement_epoch,reversal_sha256,target_sha256,fresh_ordinary_plan_sha256,compatible_intent_sha256,payload) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) ON CONFLICT(ordinary_restoration_plan_sha256) DO NOTHING`, p.digest, r.OrdinaryRestorationPlanSHA256, r.B0ReactivationPlanSHA256, p.document.B0ReceiptSHA256, r.SourceRevision, p.document.RetirementEpoch, p.document.ReversalSHA256, p.document.TargetSHA256, fresh, compatible, p.body)
		if err != nil {
			return err
		}
		result, err = loadColdOrdinaryFinalization(ctx, tx, approved, r.SourceRevision)
		return err
	})
	return result, err
}
func InspectColdOrdinaryFinalizationPlan(ctx context.Context, pool *pgxpool.Pool, digest, source string) (*ColdOrdinaryFinalizationPlan, error) {
	var result *ColdOrdinaryFinalizationPlan
	err := inspectColdB0Reactivation(ctx, pool, digest, source, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = loadColdOrdinaryFinalization(ctx, tx, digest, source)
		return err
	})
	return result, err
}
