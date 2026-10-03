package queue

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"errors"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

//go:embed joint_target.sql
var jointTargetSchema string

//go:embed joint_admission.lua
var jointAdmissionLua string

type jointOwnership struct {
	intent string
	spec   ColdTransitionSpec
	target *ColdB0Target
}

// OpenJointOwnedAuthority retains a separately installed, source-pinned actual
// B0 audit. No journal, epoch, target or owner is adopted from a latest selector.
// The exact active ordinary plan determines its immutable joint journal binding.
func OpenJointOwnedAuthority(ctx context.Context, dsn string, c *Client, epoch int64, digest, revision string, lua []byte) (*Authority, error) {
	hash := sha256.Sum256(lua)
	if hex.EncodeToString(hash[:]) != coldB0LuaSHA256 {
		return nil, ErrConfiguration
	}
	return openOwnedAuthority(ctx, dsn, c, epoch, digest, revision, string(lua))
}

func (a *Authority) loadJointOwnership(ctx context.Context, tx pgx.Tx, p *OwnershipPlan) (*jointOwnership, error) {
	var digest, body, phase string
	err := tx.QueryRow(ctx, `SELECT intent_sha256,payload,phase FROM public.crawler_ownership_transition
 WHERE reserved_plan_sha256=$1 AND routing_epoch=$2 AND source_revision=$3`, p.digest, p.Epoch(), p.SourceRevision()).Scan(&digest, &body, &phase)
	if errors.Is(err, pgx.ErrNoRows) {
		// An unjournalled foundation owner is valid only outside any unfinished
		// joint transition. Intent begins while every writer is already cold.
		if err := requireNoOpenJointOwnership(ctx, tx); err != nil {
			return nil, err
		}
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if phase != "active" || a.jointAuditLua == "" {
		return nil, ErrAuthorityLost
	}
	s, err := decodeColdTransition(body, digest)
	if err != nil || s.SourceRevision != p.SourceRevision() {
		return nil, ErrAuthorityLost
	}
	var targetBody string
	err = tx.QueryRow(ctx, "SELECT payload FROM public.crawler_ownership_b0_target WHERE target_sha256=$1", s.TargetB0ManifestSHA256).Scan(&targetBody)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	t, err := DecodeColdB0Target(targetBody, s.TargetB0ManifestSHA256, []byte(a.jointAuditLua))
	if err != nil {
		return nil, ErrAuthorityLost
	}
	if err := t.attest(ctx, tx, a.queue, p); err != nil {
		return nil, err
	}
	return &jointOwnership{digest, s, t}, nil
}

func requireNoOpenJointOwnership(ctx context.Context, tx pgx.Tx) error {
	var open bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM public.crawler_ownership_transition WHERE phase NOT IN ('reversed','superseded'))").Scan(&open); err != nil {
		return err
	}
	if open {
		return ErrAuthorityLost
	}
	return nil
}

func (c *Client) verifyJointOwnership(ctx context.Context, p *OwnershipPlan) error {
	j := p.joint
	keys := append(j.target.keys(), ownershipProjectionKey, coldPublicationKey)
	args := append(j.target.auditArguments(p.Epoch()), p.body, publicationMarker(j.intent, j.spec, p, "published"))
	script := "local function audited_b0()\n" + j.target.lua + "\nend\n" + jointAdmissionLua
	value, err := c.redis.Eval(ctx, script, keys, args...).Text()
	if err != nil {
		var rejected redis.Error
		if errors.As(err, &rejected) {
			return ErrAuthorityLost
		}
		return ErrObservation
	}
	if value != "accepted" {
		return ErrAuthorityLost
	}
	return nil
}
