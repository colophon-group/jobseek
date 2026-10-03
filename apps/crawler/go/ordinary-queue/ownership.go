package queue

import (
	"bytes"
	"context"
	"crypto/sha1"
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"sort"

	"github.com/jackc/pgx/v5"
)

const ownershipVersion = "jobseek.ordinary.ownership/v1"
const greenhouseOwnershipProfile = "greenhouse.token-skip/v1"

//go:embed ownership.sql
var ownershipSchema string

var ownershipSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)
var ownershipRevision = regexp.MustCompile(`^[0-9a-f]{40}$`)

type ownershipMember struct {
	BoardID             string            `json:"board_id"`
	CompanyID           string            `json:"company_id"`
	Domain              string            `json:"domain"`
	Kind                Kind              `json:"kind"`
	Worker              WorkerType        `json:"worker"`
	Profile             string            `json:"profile"`
	EffectiveConfigHash string            `json:"effective_config_sha256"`
	Config              map[string]string `json:"config"`
}
type ownershipDocument struct {
	Version        string            `json:"version"`
	Epoch          int64             `json:"routing_epoch"`
	SourceRevision string            `json:"source_revision"`
	Members        []ownershipMember `json:"members"`
}

// OwnershipPlan is an immutable durable document, never a queue/write grant.
// Staging cannot activate it. A supported quiesced transaction must bind
// the existing B0 epoch, exact revision and both owners' Redis projection.
type OwnershipPlan struct {
	document       ownershipDocument
	body           string
	digest         string
	projectionHash string
}

func (p *OwnershipPlan) SHA256() string {
	if p == nil {
		return ""
	}
	return p.digest
}
func (p *OwnershipPlan) Epoch() int64 {
	if p == nil {
		return 0
	}
	return p.document.Epoch
}
func (p *OwnershipPlan) SourceRevision() string {
	if p == nil {
		return ""
	}
	return p.document.SourceRevision
}
func (p *OwnershipPlan) MemberCount() int {
	if p == nil {
		return 0
	}
	return len(p.document.Members)
}

// ProjectionSHA1 binds Redis's available byte-integrity check to the exact
// SHA256-attested durable payload. It is not a credential or write capability.
func (p *OwnershipPlan) ProjectionSHA1() string {
	if p == nil {
		return ""
	}
	return p.projectionHash
}

func decodeOwnership(body, digest string) (*OwnershipPlan, error) {
	if len(body) == 0 || len(body) > 16<<20 || !ownershipSHA256.MatchString(digest) {
		return nil, ErrAuthorityLost
	}
	hash := sha256.Sum256([]byte(body))
	if hex.EncodeToString(hash[:]) != digest {
		return nil, ErrAuthorityLost
	}
	var doc ownershipDocument
	decoder := json.NewDecoder(bytes.NewBufferString(body))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&doc) != nil || decoder.Decode(new(any)) != io.EOF || doc.Version != ownershipVersion || doc.Epoch < 1 || doc.Epoch > 9999999999999 || !ownershipRevision.MatchString(doc.SourceRevision) || len(doc.Members) < 1 || len(doc.Members) > 20000 {
		return nil, ErrAuthorityLost
	}
	previous := ""
	for _, member := range doc.Members {
		key := string(member.Kind) + "|" + member.BoardID
		if key <= previous || member.Kind != Monitor || member.Worker != Simple || member.Profile != greenhouseOwnershipProfile {
			return nil, ErrAuthorityLost
		}
		profile, err := InspectGreenhouseMonitor(member.BoardID, member.Config)
		if err != nil || profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
			return nil, ErrAuthorityLost
		}
		previous = key
	}
	canonical, err := json.Marshal(doc)
	// This rejects duplicate keys, unknown fields and alternate numeric/string
	// interpretations, even when a direct SQL writer supplied matching hashes.
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	projectionHash := sha1.Sum([]byte(body))
	return &OwnershipPlan{document: doc, body: body, digest: digest, projectionHash: hex.EncodeToString(projectionHash[:])}, nil
}

// StageGreenhouseOwnership captures canonical enabled profiles under the lease,
// epoch and row barriers, persisting only a staged, exact source/epoch document.
// No caller-selected revision/plan can become active through this API.
func (a *Authority) StageGreenhouseOwnership(ctx context.Context, revision string, boardIDs []string) (*OwnershipPlan, error) {
	if a == nil || a.pool == nil || a.queue == nil || !ownershipRevision.MatchString(revision) || len(boardIDs) < 1 || len(boardIDs) > 20000 {
		return nil, ErrConfiguration
	}
	ids := append([]string(nil), boardIDs...)
	sort.Strings(ids)
	for index, id := range ids {
		if !canonicalUUID.MatchString(id) || (index > 0 && ids[index-1] == id) {
			return nil, ErrConfiguration
		}
	}
	var plan *OwnershipPlan
	err := a.transaction(ctx, true, func(ctx context.Context, tx pgx.Tx) error {
		doc := ownershipDocument{Version: ownershipVersion, Epoch: a.epoch, SourceRevision: revision}
		for _, id := range ids {
			profile, cached, err := a.observeGreenhouseMonitor(ctx, tx, id)
			if err != nil {
				return err
			}
			metadata, err := profileMetadata(cached["metadata"])
			if err != nil {
				return err
			}
			stable, err := stableGreenhouseConfig(cached, metadata)
			if err != nil {
				return err
			}
			doc.Members = append(doc.Members, ownershipMember{id, profile.CompanyID, profile.Domain, Monitor, Simple, greenhouseOwnershipProfile, profile.EffectiveConfigSHA256, stable})
		}
		body, err := json.Marshal(doc)
		if err != nil {
			return ErrConfiguration
		}
		hash := sha256.Sum256(body)
		plan, err = decodeOwnership(string(body), hex.EncodeToString(hash[:]))
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `INSERT INTO public.ordinary_worker_ownership_plan
  (plan_sha256,routing_epoch,source_revision,payload) VALUES($1,$2,$3,$4)
  ON CONFLICT(plan_sha256) DO NOTHING`, plan.digest, a.epoch, revision, plan.body)
		if err != nil {
			return err
		}
		var state string
		if err := tx.QueryRow(ctx, "SELECT state FROM public.ordinary_worker_ownership_plan WHERE plan_sha256=$1", plan.digest).Scan(&state); err != nil {
			return err
		}
		if state != "staged" {
			return ErrAuthorityLost
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return plan, nil
}

// LoadActiveOwnership requires an externally attested exact revision and plan,
// plus the Authority's already verified epoch. It never adopts the latest
// allocator value or reconstructs a lost Redis projection automatically.
func (a *Authority) LoadActiveOwnership(ctx context.Context, digest, revision string) (*OwnershipPlan, error) {
	if a == nil || a.pool == nil || a.queue == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	var plan *OwnershipPlan
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		plan, err = a.loadActiveOwnership(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return plan, nil
}

func (a *Authority) loadActiveOwnership(ctx context.Context, tx pgx.Tx, digest, revision string) (*OwnershipPlan, error) {
	var body string
	// The shared lease barrier already prevents any state transition. Avoid a
	// row lock: a direct transition trigger can hold the row while waiting
	// for that barrier, so acquiring both here would invert their order.
	err := tx.QueryRow(ctx, `SELECT payload FROM public.ordinary_worker_ownership_plan
  WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state='active'`, digest, revision, a.epoch).Scan(&body)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrAuthorityLost
	}
	if err != nil {
		return nil, err
	}
	p, err := decodeOwnership(body, digest)
	if err != nil {
		return nil, err
	}
	return p, nil
}

// InspectStagedOwnership is exact, fresh candidate readback, never an owner or
// write grant. It rejects active/retired plans and changed canonical eligibility.
// The existing shared lease/epoch barriers protect the document and allocator;
// no plan row lock inverts the state-transition trigger's barrier order.
func (a *Authority) InspectStagedOwnership(ctx context.Context, digest, revision string) (*OwnershipPlan, error) {
	if a == nil || a.pool == nil || a.queue == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	var plan *OwnershipPlan
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		var body string
		err := tx.QueryRow(ctx, `SELECT payload FROM public.ordinary_worker_ownership_plan
 WHERE plan_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND state='staged'`, digest, revision, a.epoch).Scan(&body)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrAuthorityLost
		}
		if err != nil {
			return err
		}
		plan, err = decodeOwnership(body, digest)
		if err != nil {
			return err
		}
		for _, member := range plan.document.Members {
			profile, _, err := a.observeGreenhouseMonitor(ctx, tx, member.BoardID)
			if err != nil {
				return err
			}
			if profile.CompanyID != member.CompanyID || profile.Domain != member.Domain || profile.EffectiveConfigSHA256 != member.EffectiveConfigHash {
				return ErrAuthorityLost
			}
		}
		return nil
	})
	if err != nil {
		return nil, authorityError(err)
	}
	return plan, nil
}

func requireUnselectedAuthority(ctx context.Context, tx pgx.Tx) error {
	var active bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM public.ordinary_worker_ownership_plan WHERE state='active')").Scan(&active); err != nil {
		return err
	}
	if active {
		return ErrAuthorityLost
	}
	return nil
}
