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

//go:embed cold_reversal.sql
var coldReversalSchema string

const coldReversalVersion = "jobseek.crawler.cold-reversal/v1"

// ColdReversalSpec binds the independently attested all-writer-cold host and
// exact retained forward/rollback release identities. These digests are not
// quiescence proof or permission to start services. Retirement deliberately does
// not depend on candidate configuration eligibility or surviving Redis witnesses.
type ColdReversalSpec struct {
	Version                    string `json:"version"`
	ReversalID                 string `json:"reversal_id"`
	ForwardIntentSHA256        string `json:"forward_intent_sha256"`
	SourceRevision             string `json:"source_revision"`
	SourceEpoch                int64  `json:"source_epoch"`
	SourcePlanSHA256           string `json:"source_plan_sha256"`
	SourcePhase                string `json:"source_phase"`
	RollbackReleaseSHA256      string `json:"rollback_release_sha256"`
	RollbackOrdinaryPlanSHA256 string `json:"rollback_ordinary_plan_sha256"`
	RollbackB0ReceiptSHA256    string `json:"rollback_b0_receipt_sha256"`
	ColdAttestationSHA256      string `json:"cold_attestation_sha256"`
}

func (s ColdReversalSpec) valid() bool {
	if s.Version != coldReversalVersion || !canonicalUUID.MatchString(s.ReversalID) || !ownershipRevision.MatchString(s.SourceRevision) || s.SourceEpoch < 1 || s.SourceEpoch >= 9999999999999 {
		return false
	}
	switch s.SourcePhase {
	case "reserved", "publishing", "published", "active":
	default:
		return false
	}
	for _, digest := range []string{s.ForwardIntentSHA256, s.SourcePlanSHA256, s.RollbackReleaseSHA256, s.ColdAttestationSHA256} {
		if !ownershipSHA256.MatchString(digest) {
			return false
		}
	}
	for _, digest := range []string{s.RollbackOrdinaryPlanSHA256, s.RollbackB0ReceiptSHA256} {
		if digest != "" && !ownershipSHA256.MatchString(digest) {
			return false
		}
	}
	return true
}

func decodeColdReversal(body, digest string) (ColdReversalSpec, error) {
	var s ColdReversalSpec
	hash := sha256.Sum256([]byte(body))
	if len(body) < 1 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(hash[:]) != digest {
		return s, ErrAuthorityLost
	}
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&s) != nil || d.Decode(new(any)) != io.EOF || !s.valid() {
		return ColdReversalSpec{}, ErrAuthorityLost
	}
	canonical, err := json.Marshal(s)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return ColdReversalSpec{}, ErrAuthorityLost
	}
	return s, nil
}

func DecodeColdReversalSpec(body, digest string) (ColdReversalSpec, error) {
	s, err := decodeColdReversal(body, digest)
	if err != nil {
		return ColdReversalSpec{}, ErrConfiguration
	}
	return s, nil
}

// ColdReversalState is retained progress, not restored ownership/readiness.
type ColdReversalState struct {
	spec       ColdReversalSpec
	digest     string
	phase      string
	retirement int64
}

func (s *ColdReversalState) Spec() ColdReversalSpec { return s.spec }
func (s *ColdReversalState) SHA256() string         { return s.digest }
func (s *ColdReversalState) Phase() string          { return s.phase }
func (s *ColdReversalState) RetirementEpoch() int64 { return s.retirement }

func loadColdReversal(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdReversalState, error) {
	var body, phase string
	var epoch *int64
	err := tx.QueryRow(ctx, `SELECT payload,phase,retirement_epoch FROM public.crawler_ownership_reversal
 WHERE reversal_sha256=$1 AND source_revision=$2`, digest, revision).Scan(&body, &phase, &epoch)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	s, err := decodeColdReversal(body, digest)
	if err != nil || s.SourceRevision != revision || (phase == "pending" && epoch != nil) || (phase == "reserved" && (epoch == nil || *epoch <= s.SourceEpoch || *epoch > 9999999999999)) || (phase != "pending" && phase != "reserved") {
		return nil, ErrAuthorityLost
	}
	state := &ColdReversalState{spec: s, digest: digest, phase: phase}
	if epoch != nil {
		state.retirement = *epoch
	}
	return state, nil
}

func reversalForwardBinding(ctx context.Context, tx pgx.Tx, s ColdReversalSpec, phase string) error {
	var body, actualPhase, plan string
	var epoch int64
	err := tx.QueryRow(ctx, `SELECT payload,phase,routing_epoch,reserved_plan_sha256
 FROM public.crawler_ownership_transition WHERE intent_sha256=$1 AND source_revision=$2`, s.ForwardIntentSHA256, s.SourceRevision).Scan(&body, &actualPhase, &epoch, &plan)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrAuthorityLost
	}
	if err != nil {
		return err
	}
	forward, err := decodeColdTransition(body, s.ForwardIntentSHA256)
	if err != nil || forward.SourceRevision != s.SourceRevision || actualPhase != phase || epoch != s.SourceEpoch || plan != s.SourcePlanSHA256 || forward.RollbackReleaseSHA256 != s.RollbackReleaseSHA256 || forward.PreviousOrdinaryPlanSHA256 != s.RollbackOrdinaryPlanSHA256 || forward.PreviousB0ReceiptSHA256 != s.RollbackB0ReceiptSHA256 {
		return ErrAuthorityLost
	}
	var payload, ownerState string
	err = tx.QueryRow(ctx, "SELECT payload,state FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3", plan, s.SourceRevision, epoch).Scan(&payload, &ownerState)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrAuthorityLost
	}
	if err != nil {
		return err
	}
	p, err := decodeOwnership(payload, plan)
	if err != nil || p.SourceRevision() != s.SourceRevision || p.Epoch() != epoch {
		return ErrAuthorityLost
	}
	if s.SourcePhase == "active" {
		if ownerState != "active" && ownerState != "retired" {
			return ErrAuthorityLost
		}
	} else if ownerState != "staged" {
		return ErrAuthorityLost
	}
	return nil
}

func reversalOwner(ctx context.Context, tx pgx.Tx, s ColdReversalSpec, reserved bool) error {
	var active string
	err := tx.QueryRow(ctx, "SELECT plan_sha256 FROM public.ordinary_worker_ownership_plan WHERE state='active'").Scan(&active)
	if reserved || s.SourcePhase != "active" {
		if !errors.Is(err, pgx.ErrNoRows) {
			if err != nil {
				return err
			}
			return ErrAuthorityLost
		}
		return nil
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrAuthorityLost
	}
	if err != nil {
		return err
	}
	if active != s.SourcePlanSHA256 {
		return ErrAuthorityLost
	}
	return nil
}

// BeginColdOwnershipReversal commits exact intent and changes the forward
// journal to reversing BEFORE touching the nontransactional sequence. Every
// supported claimant remains blocked through restoration. It neither repairs
// missing Redis evidence nor revalidates a now-disabled candidate to retire it.
func BeginColdOwnershipReversal(ctx context.Context, pool *pgxpool.Pool, s ColdReversalSpec) (string, error) {
	if !s.valid() {
		return "", ErrConfiguration
	}
	body, err := json.Marshal(s)
	if err != nil {
		return "", ErrConfiguration
	}
	hash := sha256.Sum256(body)
	digest := hex.EncodeToString(hash[:])
	err = coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var existing string
		err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_reversal WHERE reversal_sha256=$1 AND source_revision=$2", digest, s.SourceRevision).Scan(&existing)
		if err == nil {
			if existing != string(body) {
				return ErrAuthorityLost
			}
			return nil // Retained exact identity, not live allocation/restore authority.
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if err := reversalForwardBinding(ctx, tx, s, s.SourcePhase); err != nil {
			return err
		}
		if err := reversalOwner(ctx, tx, s, false); err != nil {
			return err
		}
		var current int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current != s.SourceEpoch {
			return ErrAuthorityLost
		}
		if _, err := tx.Exec(ctx, `INSERT INTO public.crawler_ownership_reversal
 (reversal_sha256,reversal_id,forward_intent_sha256,source_revision,source_epoch,source_plan_sha256,source_phase,payload)
 VALUES($1,$2::uuid,$3,$4,$5,$6,$7,$8)`, digest, s.ReversalID, s.ForwardIntentSHA256, s.SourceRevision, s.SourceEpoch, s.SourcePlanSHA256, s.SourcePhase, string(body)); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, "UPDATE public.crawler_ownership_transition SET phase='reversing' WHERE intent_sha256=$1 AND phase=$2", s.ForwardIntentSHA256, s.SourcePhase)
		return err
	})
	if err != nil {
		return "", err
	}
	return digest, nil
}

// ReserveColdReversalEpoch retires the exact active ordinary owner and records
// a fresh shared retirement epoch atomically. Rolled-back nextval burns an epoch;
// recovery allocates another fresh value rather than adopting the high-water.
// Retry verifies its recorded epoch and no active owner, never reallocating.
// It leaves canonical rows/receipts/deadlines and EVERY Redis key untouched.
func ReserveColdReversalEpoch(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdReversalState, error) {
	if !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	var result *ColdReversalState
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdReversal(ctx, tx, digest, revision)
		if err != nil {
			return err
		}
		if err := reversalForwardBinding(ctx, tx, state.spec, "reversing"); err != nil {
			return err
		}
		if err := reversalOwner(ctx, tx, state.spec, state.phase == "reserved"); err != nil {
			return err
		}
		var current int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current < state.spec.SourceEpoch {
			return ErrAuthorityLost
		}
		if state.phase == "reserved" {
			if current != state.retirement {
				return ErrAuthorityLost
			}
			result = state
			return nil
		}
		if current >= 9999999999999 {
			return ErrAuthorityLost
		}
		var epoch int64
		if err := tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch); err != nil {
			return err
		}
		if epoch <= current || epoch > 9999999999999 {
			return ErrAuthorityLost
		}
		if state.spec.SourcePhase == "active" {
			if _, err := tx.Exec(ctx, "UPDATE public.ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", state.spec.SourcePlanSHA256); err != nil {
				return err
			}
		}
		if _, err := tx.Exec(ctx, "UPDATE public.crawler_ownership_reversal SET phase='reserved',retirement_epoch=$2 WHERE reversal_sha256=$1 AND phase='pending'", digest, epoch); err != nil {
			return err
		}
		state.phase, state.retirement = "reserved", epoch
		result = state
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// InspectColdReversal reads exact retained progress even after witness loss or
// a burned epoch. It grants no allocator/current-state or restore authority.
func InspectColdReversal(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdReversalState, error) {
	if pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var result *ColdReversalState
	// A single MVCC row observation needs no mutation barriers or current
	// allocator. It remains readable while an uncommitted reservation is paused.
	err := pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		var err error
		result, err = loadColdReversal(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, authorityError(err)
	}
	return result, nil
}
