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
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed cold_ordinary_restoration.sql
var coldOrdinaryRestorationSchema string

type ColdOrdinaryRestorationRequest struct {
	ReversalSHA256          string `json:"reversal_sha256"`
	SourceRevision          string `json:"source_revision"`
	RetirementEpoch         int64  `json:"retirement_epoch"`
	B0RestorationPlanSHA256 string `json:"b0_restoration_plan_sha256"`
	RollbackSourceRevision  string `json:"rollback_source_revision"`
}

func (r ColdOrdinaryRestorationRequest) valid() bool {
	return ownershipSHA256.MatchString(r.ReversalSHA256) && ownershipRevision.MatchString(r.SourceRevision) && r.RetirementEpoch >= 2 && r.RetirementEpoch <= 9999999999999 && ownershipSHA256.MatchString(r.B0RestorationPlanSHA256) && (r.RollbackSourceRevision == "" || ownershipRevision.MatchString(r.RollbackSourceRevision))
}

// DecodeColdOrdinaryRestorationRequest binds a protected canonical request.
func DecodeColdOrdinaryRestorationRequest(body, digest string) (ColdOrdinaryRestorationRequest, error) {
	var r ColdOrdinaryRestorationRequest
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return r, ErrAuthorityLost
	}
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || d.Decode(new(any)) != io.EOF || !r.valid() {
		return ColdOrdinaryRestorationRequest{}, ErrAuthorityLost
	}
	canonical, err := json.Marshal(r)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return ColdOrdinaryRestorationRequest{}, ErrAuthorityLost
	}
	return r, nil
}

type coldOrdinaryRestorationDocument struct {
	Version                    string                         `json:"version"`
	Request                    ColdOrdinaryRestorationRequest `json:"request"`
	Mode                       string                         `json:"mode"`
	RollbackReleaseSHA256      string                         `json:"rollback_release_sha256"`
	PreviousOrdinaryPlanSHA256 string                         `json:"previous_ordinary_plan_sha256"`
	PreviousOwner              *ownershipDocument             `json:"previous_owner"`
	FreshOrdinaryPlanSHA256    string                         `json:"fresh_ordinary_plan_sha256"`
	FreshOwner                 *ownershipDocument             `json:"fresh_owner"`
}

// ColdOrdinaryRestorationPlan is retained preparation, not restored ownership.
// It never reactivates a retired plan or releases an unfinished joint reversal.
type ColdOrdinaryRestorationPlan struct {
	document     coldOrdinaryRestorationDocument
	body, digest string
	fresh        *OwnershipPlan
}

func (p *ColdOrdinaryRestorationPlan) SHA256() string  { return p.digest }
func (p *ColdOrdinaryRestorationPlan) Payload() string { return p.body }
func (p *ColdOrdinaryRestorationPlan) Request() ColdOrdinaryRestorationRequest {
	return p.document.Request
}
func (p *ColdOrdinaryRestorationPlan) Mode() string                       { return p.document.Mode }
func (p *ColdOrdinaryRestorationPlan) FreshOwnershipPlan() *OwnershipPlan { return p.fresh }

func decodeColdOrdinaryRestoration(body, digest string) (*ColdOrdinaryRestorationPlan, error) {
	h := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 64*1024*1024 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return nil, ErrAuthorityLost
	}
	var doc coldOrdinaryRestorationDocument
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&doc) != nil || d.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-ordinary-restoration/v1" || !doc.Request.valid() || !ownershipSHA256.MatchString(doc.RollbackReleaseSHA256) {
		return nil, ErrAuthorityLost
	}
	p := &ColdOrdinaryRestorationPlan{document: doc, body: body, digest: digest}
	switch doc.Mode {
	case "legacy":
		if doc.PreviousOrdinaryPlanSHA256 != "" || doc.PreviousOwner != nil || doc.FreshOrdinaryPlanSHA256 != "" || doc.FreshOwner != nil || doc.Request.RollbackSourceRevision != "" {
			return nil, ErrAuthorityLost
		}
	case "native":
		if doc.PreviousOwner == nil || doc.FreshOwner == nil {
			return nil, ErrAuthorityLost
		}
		oldBody, err := json.Marshal(doc.PreviousOwner)
		if err != nil {
			return nil, ErrAuthorityLost
		}
		old, err := decodeOwnership(string(oldBody), doc.PreviousOrdinaryPlanSHA256)
		if err != nil {
			return nil, err
		}
		freshBody, err := json.Marshal(doc.FreshOwner)
		if err != nil {
			return nil, ErrAuthorityLost
		}
		p.fresh, err = decodeOwnership(string(freshBody), doc.FreshOrdinaryPlanSHA256)
		if err != nil {
			return nil, err
		}
		if old.SourceRevision() != doc.Request.RollbackSourceRevision || p.fresh.SourceRevision() != old.SourceRevision() || old.Epoch() >= doc.Request.RetirementEpoch || p.fresh.Epoch() != doc.Request.RetirementEpoch || old.MemberCount() != p.fresh.MemberCount() {
			return nil, ErrAuthorityLost
		}
		for i, m := range old.document.Members {
			fresh := p.fresh.document.Members[i]
			if m.BoardID != fresh.BoardID || m.CompanyID != fresh.CompanyID || m.Kind != fresh.Kind || m.Worker != fresh.Worker || m.Profile != fresh.Profile {
				return nil, ErrAuthorityLost
			}
		}
	default:
		return nil, ErrAuthorityLost
	}
	wanted, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(wanted, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return p, nil
}

func ordinaryRestorationContext(ctx context.Context, tx pgx.Tx, c *Client, r ColdOrdinaryRestorationRequest, target *ColdB0Target) (*ColdReversalState, error) {
	state, err := loadColdReversal(ctx, tx, r.ReversalSHA256, r.SourceRevision)
	if err != nil {
		return nil, err
	}
	b0, err := loadColdB0Restoration(ctx, tx, r.B0RestorationPlanSHA256, r.SourceRevision)
	if err != nil {
		return nil, err
	}
	if state.phase != "reserved" || state.retirement != r.RetirementEpoch || b0.phase != "fences-cleared" || b0.plan.document.Request.ReversalSHA256 != r.ReversalSHA256 || b0.plan.document.Request.RetirementEpoch != r.RetirementEpoch || b0.plan.document.TargetSHA256 != target.digest {
		return nil, ErrAuthorityLost
	}
	if err := requireColdB0RollbackContext(ctx, tx, b0.plan.document.Request, target); err != nil {
		return nil, err
	}
	if witness, err := coldB0RestoreWitness(ctx, c, target, b0.plan); err != nil || witness != "restored" {
		if err != nil {
			return nil, err
		}
		return nil, ErrAuthorityLost
	}
	return state, nil
}

func deriveColdOrdinaryRestoration(ctx context.Context, tx pgx.Tx, c *Client, r ColdOrdinaryRestorationRequest, target *ColdB0Target) (*ColdOrdinaryRestorationPlan, error) {
	state, err := ordinaryRestorationContext(ctx, tx, c, r, target)
	if err != nil {
		return nil, err
	}
	doc := coldOrdinaryRestorationDocument{Version: "jobseek.crawler.cold-ordinary-restoration/v1", Request: r, Mode: "legacy", RollbackReleaseSHA256: state.spec.RollbackReleaseSHA256, PreviousOrdinaryPlanSHA256: state.spec.RollbackOrdinaryPlanSHA256}
	if doc.PreviousOrdinaryPlanSHA256 == "" {
		if r.RollbackSourceRevision != "" {
			return nil, ErrAuthorityLost
		}
	} else {
		var oldBody string
		err := tx.QueryRow(ctx, "SELECT payload FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND source_revision=$2 AND state='retired'", doc.PreviousOrdinaryPlanSHA256, r.RollbackSourceRevision).Scan(&oldBody)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrAuthorityLost
		}
		if err != nil {
			return nil, err
		}
		old, err := decodeOwnership(oldBody, doc.PreviousOrdinaryPlanSHA256)
		if err != nil {
			return nil, err
		}
		var previousEpoch int64
		if err := tx.QueryRow(ctx, "SELECT previous_epoch FROM public.crawler_ownership_transition WHERE intent_sha256=$1", state.spec.ForwardIntentSHA256).Scan(&previousEpoch); err != nil {
			return nil, err
		}
		if old.Epoch() != previousEpoch {
			return nil, ErrAuthorityLost
		}
		doc.Mode, doc.PreviousOwner = "native", &old.document
		fresh := ownershipDocument{Version: ownershipVersion, Epoch: r.RetirementEpoch, SourceRevision: r.RollbackSourceRevision, Members: []ownershipMember{}}
		a := &Authority{queue: c}
		for _, m := range old.document.Members {
			for _, b := range target.document.Boards {
				if b.ID == m.BoardID {
					return nil, ErrAuthorityLost
				}
			}
			profile, cached, err := a.observeGreenhouseMonitor(ctx, tx, m.BoardID)
			if err != nil {
				return nil, err
			}
			if profile.CompanyID != m.CompanyID {
				return nil, ErrAuthorityLost
			}
			metadata, err := profileMetadata(cached["metadata"])
			if err != nil {
				return nil, err
			}
			config, err := stableGreenhouseConfig(cached, metadata)
			if err != nil {
				return nil, err
			}
			fresh.Members = append(fresh.Members, ownershipMember{m.BoardID, profile.CompanyID, profile.Domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, config})
		}
		body, err := json.Marshal(fresh)
		if err != nil {
			return nil, err
		}
		h := sha256.Sum256(body)
		doc.FreshOrdinaryPlanSHA256, doc.FreshOwner = hex.EncodeToString(h[:]), &fresh
		if _, err := decodeOwnership(string(body), doc.FreshOrdinaryPlanSHA256); err != nil {
			return nil, err
		}
	}
	// Row locks protect canonical profiles. Re-observe every Redis-backed
	// ordinary profile after the complete cohort has been assembled, and refuse
	// live canonical leases while the joint reversal still excludes writers.
	if doc.FreshOwner != nil {
		a := &Authority{queue: c}
		boards := make([]string, 0, len(doc.FreshOwner.Members))
		for _, member := range doc.FreshOwner.Members {
			profile, cached, err := a.observeGreenhouseMonitor(ctx, tx, member.BoardID)
			if err != nil {
				return nil, err
			}
			metadata, err := profileMetadata(cached["metadata"])
			if err != nil {
				return nil, err
			}
			config, err := stableGreenhouseConfig(cached, metadata)
			if err != nil {
				return nil, err
			}
			wanted, err := json.Marshal(member.Config)
			if err != nil {
				return nil, err
			}
			observed, err := json.Marshal(config)
			if err != nil {
				return nil, err
			}
			if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash || !bytes.Equal(wanted, observed) {
				return nil, ErrAuthorityLost
			}
			boards = append(boards, member.BoardID)
		}
		var leased bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM public.job_board WHERE id=ANY($1::uuid[]) AND leased_until>now()) OR EXISTS(SELECT 1 FROM public.job_posting WHERE board_id=ANY($1::uuid[]) AND leased_until>now())`, boards).Scan(&leased); err != nil {
			return nil, err
		}
		if leased {
			return nil, ErrAuthorityLost
		}
	}
	// Re-observe exact B0 restoration and allocator context last.
	if _, err := ordinaryRestorationContext(ctx, tx, c, r, target); err != nil {
		return nil, err
	}
	body, err := json.Marshal(doc)
	if err != nil {
		return nil, err
	}
	h := sha256.Sum256(body)
	return decodeColdOrdinaryRestoration(string(body), hex.EncodeToString(h[:]))
}

func BuildColdOrdinaryRestorationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, r ColdOrdinaryRestorationRequest, target *ColdB0Target) (*ColdOrdinaryRestorationPlan, error) {
	if ctx == nil || c == nil || target == nil || !r.valid() {
		return nil, ErrConfiguration
	}
	var result *ColdOrdinaryRestorationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = deriveColdOrdinaryRestoration(ctx, tx, c, r, target)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

func loadColdOrdinaryRestoration(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdOrdinaryRestorationPlan, error) {
	var body string
	err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_ordinary_restoration WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	plan, err := decodeColdOrdinaryRestoration(body, digest)
	if err != nil || plan.document.Request.SourceRevision != revision {
		return nil, ErrAuthorityLost
	}
	return plan, nil
}

// RetainColdOrdinaryRestorationPlan freshly re-derives exact approval and commits
// the fresh staged native plan together with immutable preparation history. No
// allocation, projection, active owner, journal completion or queue repair occurs.
func RetainColdOrdinaryRestorationPlan(ctx context.Context, pool *pgxpool.Pool, c *Client, r ColdOrdinaryRestorationRequest, target *ColdB0Target, approved string) (*ColdOrdinaryRestorationPlan, error) {
	if ctx == nil || c == nil || target == nil || !r.valid() || !ownershipSHA256.MatchString(approved) {
		return nil, ErrConfiguration
	}
	var result *ColdOrdinaryRestorationPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		plan, err := deriveColdOrdinaryRestoration(ctx, tx, c, r, target)
		if err != nil {
			return err
		}
		if plan.digest != approved {
			return ErrAuthorityLost
		}
		var freshDigest any
		if plan.fresh != nil {
			freshDigest = plan.fresh.digest
			if _, err := tx.Exec(ctx, `INSERT INTO public.ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4) ON CONFLICT(plan_sha256) DO NOTHING`, plan.fresh.digest, r.RetirementEpoch, r.RollbackSourceRevision, plan.fresh.body); err != nil {
				return err
			}
			var body, state string
			if err := tx.QueryRow(ctx, "SELECT payload,state FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.fresh.digest).Scan(&body, &state); err != nil {
				return err
			}
			if body != plan.fresh.body || state != "staged" {
				return ErrAuthorityLost
			}
		}
		if _, err := tx.Exec(ctx, `INSERT INTO public.crawler_ownership_ordinary_restoration(plan_sha256,reversal_sha256,source_revision,retirement_epoch,b0_restoration_plan_sha256,fresh_ordinary_plan_sha256,payload) VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(reversal_sha256) DO NOTHING`, plan.digest, r.ReversalSHA256, r.SourceRevision, r.RetirementEpoch, r.B0RestorationPlanSHA256, freshDigest, plan.body); err != nil {
			return err
		}
		result, err = loadColdOrdinaryRestoration(ctx, tx, approved, r.SourceRevision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// Inspection preserves exact historical preparation without allocator/lease
// barriers, Redis observations or any authority to release writers.
func InspectColdOrdinaryRestorationPlan(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdOrdinaryRestorationPlan, error) {
	if ctx == nil || pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var result *ColdOrdinaryRestorationPlan
	err := pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		var err error
		result, err = loadColdOrdinaryRestoration(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
