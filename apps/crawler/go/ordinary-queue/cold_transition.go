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

//go:embed cold_transition.sql
var coldTransitionSchema string

const coldTransitionVersion = "jobseek.crawler.cold-transition/v1"

// ColdTransitionSpec binds a protected host coordinator's independently verified
// ADR006 evidence. Digests do not prove quiescence or authenticate a release.
// These internal database primitives have no production command/startup path;
// the supported host wrapper must hold its mutation lock and attest ALL writers
// stopped before reservation, publication, recovery or reversal.
type ColdTransitionSpec struct {
	Version                    string `json:"version"`
	TransitionID               string `json:"transition_id"`
	SourceRevision             string `json:"source_revision"`
	PreviousEpoch              int64  `json:"previous_epoch"`
	PreviousOrdinaryPlanSHA256 string `json:"previous_ordinary_plan_sha256"`
	PreparedPlanSHA256         string `json:"prepared_plan_sha256"`
	PreviousB0ReceiptSHA256    string `json:"previous_b0_receipt_sha256"`
	TargetB0ManifestSHA256     string `json:"target_b0_manifest_sha256"`
	ActiveReleaseSHA256        string `json:"active_release_sha256"`
	TargetReleaseSHA256        string `json:"target_release_sha256"`
	RollbackReleaseSHA256      string `json:"rollback_release_sha256"`
	ColdAttestationSHA256      string `json:"cold_attestation_sha256"`
}

func (s ColdTransitionSpec) valid() bool {
	if s.Version != coldTransitionVersion || !canonicalUUID.MatchString(s.TransitionID) || !ownershipRevision.MatchString(s.SourceRevision) || s.PreviousEpoch < 1 || s.PreviousEpoch >= 9999999999999 {
		return false
	}
	for _, hash := range []string{s.PreparedPlanSHA256, s.TargetB0ManifestSHA256, s.ActiveReleaseSHA256, s.TargetReleaseSHA256, s.RollbackReleaseSHA256, s.ColdAttestationSHA256} {
		if !ownershipSHA256.MatchString(hash) {
			return false
		}
	}
	for _, hash := range []string{s.PreviousOrdinaryPlanSHA256, s.PreviousB0ReceiptSHA256} {
		if hash != "" && !ownershipSHA256.MatchString(hash) {
			return false
		}
	}
	return true
}

func decodeColdTransition(body, digest string) (ColdTransitionSpec, error) {
	var s ColdTransitionSpec
	hash := sha256.Sum256([]byte(body))
	if len(body) == 0 || len(body) > 4096 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(hash[:]) != digest {
		return s, ErrAuthorityLost
	}
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&s) != nil || d.Decode(new(any)) != io.EOF || !s.valid() {
		return ColdTransitionSpec{}, ErrAuthorityLost
	}
	canonical, err := json.Marshal(s)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return ColdTransitionSpec{}, ErrAuthorityLost
	}
	return s, nil
}

func coldTransitionTransaction(ctx context.Context, pool *pgxpool.Pool, fn func(context.Context, pgx.Tx) error) error {
	if pool == nil {
		return ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	return authorityError(pgx.BeginFunc(ctx, pool, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SET LOCAL idle_in_transaction_session_timeout='15s'"); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", OrdinaryLeaseBarrier); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", routingEpochBarrier); err != nil {
			return err
		}
		if err := fn(ctx, tx); err != nil {
			return err
		}
		return ctx.Err()
	}))
}

func preparedColdPlan(ctx context.Context, tx pgx.Tx, client *Client, s ColdTransitionSpec) (*OwnershipPlan, error) {
	var body string
	err := tx.QueryRow(ctx, `SELECT payload FROM public.ordinary_worker_ownership_plan
 WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state='staged'`, s.PreparedPlanSHA256, s.SourceRevision, s.PreviousEpoch).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	p, err := decodeOwnership(body, s.PreparedPlanSHA256)
	if err != nil {
		return nil, err
	}
	a := &Authority{queue: client}
	for _, m := range p.document.Members {
		profile, _, err := a.observeGreenhouseMonitor(ctx, tx, m.BoardID)
		if err != nil {
			return nil, err
		}
		if profile.CompanyID != m.CompanyID || profile.Domain != m.Domain || profile.EffectiveConfigSHA256 != m.EffectiveConfigHash {
			return nil, ErrAuthorityLost
		}
	}
	return p, nil
}

func checkPreviousColdOwner(ctx context.Context, tx pgx.Tx, s ColdTransitionSpec) error {
	var digest string
	err := tx.QueryRow(ctx, "SELECT plan_sha256 FROM public.ordinary_worker_ownership_plan WHERE state='active'").Scan(&digest)
	if errors.Is(err, pgx.ErrNoRows) && s.PreviousOrdinaryPlanSHA256 == "" {
		return nil
	}
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrAuthorityLost
		}
		return err
	}
	if digest != s.PreviousOrdinaryPlanSHA256 {
		return ErrAuthorityLost
	}
	var epoch int64
	if err := tx.QueryRow(ctx, "SELECT routing_epoch FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1", digest).Scan(&epoch); err != nil {
		return err
	}
	if epoch != s.PreviousEpoch {
		return ErrAuthorityLost
	}
	return nil
}

// BeginColdOwnershipTransition commits intent BEFORE touching the nontransactional
// sequence. Retry requires identical canonical identity; there is no latest-plan
// adoption. The current staged cohort is re-attested from PG AND Redis.
func BeginColdOwnershipTransition(ctx context.Context, pool *pgxpool.Pool, client *Client, s ColdTransitionSpec) (string, error) {
	if client == nil || !s.valid() {
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
		err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_transition WHERE intent_sha256=$1", digest).Scan(&existing)
		if err == nil {
			if existing != string(body) {
				return ErrAuthorityLost
			}
			return nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		var epoch int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&epoch, &called); err != nil {
			return err
		}
		if !called || epoch != s.PreviousEpoch {
			return ErrAuthorityLost
		}
		if err := checkPreviousColdOwner(ctx, tx, s); err != nil {
			return err
		}
		if _, err := preparedColdPlan(ctx, tx, client, s); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO public.crawler_ownership_transition
 (intent_sha256,transition_id,source_revision,previous_epoch,prepared_plan_sha256,payload)
 VALUES($1,$2::uuid,$3,$4,$5,$6)`, digest, s.TransitionID, s.SourceRevision, s.PreviousEpoch, s.PreparedPlanSHA256, string(body))
		return err
	})
	if err != nil {
		return "", err
	}
	return digest, nil
}

// ReserveColdOwnershipEpoch operates only on an exact durable pending intent.
// A rolled-back nextval can leave a higher high-water: recovery always allocates
// ANOTHER fresh epoch, never adopts the burned value. Existing reservation retries
// inspect that exact epoch/document instead of allocating again. Old ordinary
// retirement, new staged plan and journal reservation commit together. This does
// not activate an owner, publish Redis, alter receipts, or restart any service.
func ReserveColdOwnershipEpoch(ctx context.Context, pool *pgxpool.Pool, client *Client, digest, revision string) (*OwnershipPlan, error) {
	if client == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	var result *OwnershipPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		var body, phase string
		var reservedEpoch *int64
		var reservedDigest *string
		err := tx.QueryRow(ctx, `SELECT payload,phase,routing_epoch,reserved_plan_sha256 FROM public.crawler_ownership_transition
 WHERE intent_sha256=$1 AND source_revision=$2`, digest, revision).Scan(&body, &phase, &reservedEpoch, &reservedDigest)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrAuthorityLost
		}
		if err != nil {
			return err
		}
		s, err := decodeColdTransition(body, digest)
		if err != nil {
			return err
		}
		var current int64
		var called bool
		if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM public.lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
			return err
		}
		if !called || current < s.PreviousEpoch {
			return ErrAuthorityLost
		}
		if phase == "reserved" {
			if reservedEpoch == nil || reservedDigest == nil || current != *reservedEpoch {
				return ErrAuthorityLost
			}
			var active bool
			if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')").Scan(&active); err != nil {
				return err
			}
			if active {
				return ErrAuthorityLost
			}
			reserved := s
			reserved.PreviousEpoch, reserved.PreparedPlanSHA256 = current, *reservedDigest
			result, err = preparedColdPlan(ctx, tx, client, reserved)
			return err
		}
		if phase != "pending" || reservedEpoch != nil || reservedDigest != nil || current >= 9999999999999 {
			return ErrAuthorityLost
		}
		if err := checkPreviousColdOwner(ctx, tx, s); err != nil {
			return err
		}
		p, err := preparedColdPlan(ctx, tx, client, s)
		if err != nil {
			return err
		}
		// Reject a competing unfinished intent BEFORE burning any sequence value.
		var open string
		if err := tx.QueryRow(ctx, "SELECT intent_sha256 FROM public.crawler_ownership_transition WHERE phase NOT IN ('reversed','superseded')").Scan(&open); err != nil {
			return err
		}
		if open != digest {
			return ErrAuthorityLost
		}
		var epoch int64
		if err := tx.QueryRow(ctx, "SELECT nextval('public.lightpanda_b0_routing_epoch_seq')").Scan(&epoch); err != nil {
			return err
		}
		if epoch <= current || epoch > 9999999999999 {
			return ErrAuthorityLost
		}
		if s.PreviousOrdinaryPlanSHA256 != "" {
			if _, err := tx.Exec(ctx, "UPDATE public.ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", s.PreviousOrdinaryPlanSHA256); err != nil {
				return err
			}
		}
		doc := p.document
		doc.Epoch = epoch
		encoded, err := json.Marshal(doc)
		if err != nil {
			return ErrConfiguration
		}
		hash := sha256.Sum256(encoded)
		result, err = decodeOwnership(string(encoded), hex.EncodeToString(hash[:]))
		if err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO public.ordinary_worker_ownership_plan(plan_sha256,routing_epoch,source_revision,payload)
 VALUES($1,$2,$3,$4)`, result.digest, epoch, revision, result.body); err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `UPDATE public.crawler_ownership_transition SET phase='reserved',routing_epoch=$2,reserved_plan_sha256=$3
 WHERE intent_sha256=$1 AND phase='pending'`, digest, epoch, result.digest)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
