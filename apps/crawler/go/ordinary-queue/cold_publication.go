package queue

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

//go:embed cold_publication.lua
var coldPublicationLua string

//go:embed cold_publication.sql
var coldPublicationSchema string

const coldPublicationKey = "crawler:ownership:transition"

type coldPublicationMarker struct {
	Version        string `json:"version"`
	State          string `json:"state"`
	IntentSHA256   string `json:"intent_sha256"`
	SourceRevision string `json:"source_revision"`
	RoutingEpoch   int64  `json:"routing_epoch"`
	PlanSHA256     string `json:"plan_sha256"`
	B0TargetSHA256 string `json:"b0_target_sha256"`
}

func publicationMarker(digest string, s ColdTransitionSpec, p *OwnershipPlan, state string) string {
	body, _ := json.Marshal(coldPublicationMarker{"jobseek.crawler.cold-publication/v1", state, digest, p.SourceRevision(), p.Epoch(), p.digest, s.TargetB0ManifestSHA256})
	return string(body)
}

type coldPublication struct {
	spec                     ColdTransitionSpec
	phase                    string
	plan                     *OwnershipPlan
	previous, previousMarker string
}

func loadColdPublication(ctx context.Context, tx pgx.Tx, c *Client, digest, revision string, target *ColdB0Target) (*coldPublication, error) {
	var body, phase, planDigest string
	var epoch int64
	err := tx.QueryRow(ctx, `SELECT payload,phase,routing_epoch,reserved_plan_sha256 FROM crawler_ownership_transition
 WHERE intent_sha256=$1 AND source_revision=$2 AND phase IN ('reserved','publishing','published','active')`, digest, revision).Scan(&body, &phase, &epoch, &planDigest)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	s, err := decodeColdTransition(body, digest)
	if err != nil {
		return nil, err
	}
	if target.digest != s.TargetB0ManifestSHA256 {
		return nil, ErrAuthorityLost
	}
	if phase != "reserved" {
		var retained string
		err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_b0_target WHERE target_sha256=$1", target.digest).Scan(&retained)
		if errors.Is(err, pgx.ErrNoRows) || err == nil && retained != target.body {
			return nil, ErrAuthorityLost
		}
		if err != nil {
			return nil, err
		}
	}
	var current int64
	var called bool
	if err := tx.QueryRow(ctx, "SELECT last_value,is_called FROM lightpanda_b0_routing_epoch_seq").Scan(&current, &called); err != nil {
		return nil, err
	}
	if !called || current != epoch {
		return nil, ErrAuthorityLost
	}
	state := "staged"
	if phase == "active" {
		state = "active"
	}
	var planBody string
	err = tx.QueryRow(ctx, `SELECT payload FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state=$4`, planDigest, revision, epoch, state).Scan(&planBody)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	p, err := decodeOwnership(planBody, planDigest)
	if err != nil {
		return nil, err
	}
	a := &Authority{queue: c}
	for _, m := range p.document.Members {
		profile, _, err := a.observeGreenhouseMonitor(ctx, tx, m.BoardID)
		if err != nil {
			return nil, err
		}
		if profile.CompanyID != m.CompanyID || profile.Domain != m.Domain || profile.EffectiveConfigSHA256 != m.EffectiveConfigHash {
			return nil, ErrAuthorityLost
		}
	}
	if err := target.attest(ctx, tx, c, p); err != nil {
		return nil, err
	}
	previous, previousMarker := "", ""
	if s.PreviousOrdinaryPlanSHA256 != "" {
		var previousSource string
		err = tx.QueryRow(ctx, `SELECT payload,source_revision FROM ordinary_worker_ownership_plan WHERE plan_sha256=$1 AND routing_epoch=$2 AND state='retired'`, s.PreviousOrdinaryPlanSHA256, s.PreviousEpoch).Scan(&previous, &previousSource)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrAuthorityLost
		}
		if err != nil {
			return nil, err
		}
		old, err := decodeOwnership(previous, s.PreviousOrdinaryPlanSHA256)
		if err != nil {
			return nil, err
		}
		var oldIntent, oldBody string
		err = tx.QueryRow(ctx, `SELECT intent_sha256,payload FROM crawler_ownership_transition
 WHERE reserved_plan_sha256=$1 AND routing_epoch=$2 AND source_revision=$3 AND phase='superseded'`, old.digest, old.Epoch(), previousSource).Scan(&oldIntent, &oldBody)
		if err == nil {
			prior, err := decodeColdTransition(oldBody, oldIntent)
			if err != nil {
				return nil, err
			}
			previousMarker = publicationMarker(oldIntent, prior, old, "published")
		} else if !errors.Is(err, pgx.ErrNoRows) {
			return nil, err
		}
	}
	return &coldPublication{s, phase, p, previous, previousMarker}, nil
}

func coldPublicationRedis(ctx context.Context, c *Client, digest string, p *coldPublication, t *ColdB0Target, operation string) (string, error) {
	keys := append(t.keys(), ownershipProjectionKey, coldPublicationKey)
	args := append(t.auditArguments(p.plan.Epoch()), operation, p.previous, p.plan.body, publicationMarker(digest, p.spec, p.plan, "pending"), publicationMarker(digest, p.spec, p.plan, "published"), p.previousMarker)
	value, err := c.redis.Eval(ctx, t.script(), keys, args...).Text()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return "", ErrAuthorityLost
		}
		return "", ErrObservation
	}
	if value != "pending" && value != "published" {
		return "", ErrProtocol
	}
	return value, nil
}

// PrepareColdOwnershipPublication records an exact Redis pending witness BEFORE
// committing 'publishing'. That committed phase is required before projection
// effects. An unfinished attempt with a missing witness is contained, not repaired.
// This cannot attest host quiescence, transfer B0 tasks, or release services.
func PrepareColdOwnershipPublication(ctx context.Context, pool *pgxpool.Pool, c *Client, digest, revision string, t *ColdB0Target) error {
	if c == nil || t == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return ErrConfiguration
	}
	return coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		p, err := loadColdPublication(ctx, tx, c, digest, revision, t)
		if err != nil {
			return err
		}
		if p.phase == "reserved" {
			if _, err := tx.Exec(ctx, `INSERT INTO public.crawler_ownership_b0_target(target_sha256,payload)
 VALUES($1,$2) ON CONFLICT(target_sha256) DO NOTHING`, t.digest, t.body); err != nil {
				return err
			}
		}
		var targetBody string
		if err := tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_b0_target WHERE target_sha256=$1", t.digest).Scan(&targetBody); err != nil {
			if errors.Is(err, pgx.ErrNoRows) {
				return ErrAuthorityLost
			}
			return err
		}
		if targetBody != t.body {
			return ErrAuthorityLost
		}
		op := "inspect"
		if p.phase == "reserved" {
			op = "prepare"
		} else if p.phase == "published" || p.phase == "active" {
			op = "inspect-published"
		}
		if _, err := coldPublicationRedis(ctx, c, digest, p, t, op); err != nil {
			return err
		}
		if p.phase == "reserved" {
			_, err = tx.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='publishing' WHERE intent_sha256=$1 AND phase='reserved'", digest)
		}
		return err
	})
}

// PublishColdOwnership writes the ordinary projection and joint witness in ONE
// Redis MSET guarded by the unmodified real B0 audit, exact producer selectors,
// route, zero inflight/dead work, and prior bytes. SAVE acknowledgement plus exact
// atomic readback precede durable 'published'. Lost SAVE/DB replies retain an
// inspectable 'publishing' journal; retry cannot reconstruct lost Redis evidence.
func PublishColdOwnership(ctx context.Context, pool *pgxpool.Pool, c *Client, digest, revision string, t *ColdB0Target) (*OwnershipPlan, error) {
	if err := PrepareColdOwnershipPublication(ctx, pool, c, digest, revision, t); err != nil {
		return nil, err
	}
	var result *OwnershipPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		p, err := loadColdPublication(ctx, tx, c, digest, revision, t)
		if err != nil {
			return err
		}
		if p.phase != "publishing" && p.phase != "published" && p.phase != "active" {
			return ErrAuthorityLost
		}
		op := "inspect-published"
		if p.phase == "publishing" {
			op = "publish"
		}
		if _, err := coldPublicationRedis(ctx, c, digest, p, t, op); err != nil {
			return err
		}
		if p.phase == "publishing" {
			if reply, err := c.redis.Save(ctx).Result(); err != nil || reply != "OK" {
				return ErrObservation
			}
			if _, err := coldPublicationRedis(ctx, c, digest, p, t, "inspect-published"); err != nil {
				return err
			}
			if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='published' WHERE intent_sha256=$1 AND phase='publishing'", digest); err != nil {
				return err
			}
		}
		result = p.plan
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// ActivateColdOwnership installs only the exact published ordinary DB owner.
// Both the ordinary owner and journal phase commit together AFTER durable Redis
// publication and fresh shared-epoch B0/profile readback. The protected host must
// still publish verified release/receipt identities and gate complete readiness.
func ActivateColdOwnership(ctx context.Context, pool *pgxpool.Pool, c *Client, digest, revision string, t *ColdB0Target) (*OwnershipPlan, error) {
	if c == nil || t == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	var result *OwnershipPlan
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		p, err := loadColdPublication(ctx, tx, c, digest, revision, t)
		if err != nil {
			return err
		}
		if p.phase != "published" && p.phase != "active" {
			return ErrAuthorityLost
		}
		if _, err := coldPublicationRedis(ctx, c, digest, p, t, "inspect-published"); err != nil {
			return err
		}
		if p.phase == "published" {
			if _, err := tx.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1 AND state='staged'", p.plan.digest); err != nil {
				return err
			}
			if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='active' WHERE intent_sha256=$1 AND phase='published'", digest); err != nil {
				return err
			}
		}
		result = p.plan
		return nil
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
