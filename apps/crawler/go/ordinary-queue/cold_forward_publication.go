package queue

import (
	"context"
	"errors"
	"reflect"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ColdB0ForwardCompletionBinding identifies immutable approval and its committed
// SAVE/readback completion. These digests grant no host or startup authority.
type ColdB0ForwardCompletionBinding struct {
	PlanSHA256    string
	ReceiptSHA256 string
}

type coldForwardPublicationApproval struct {
	binding ColdB0ForwardCompletionBinding
	control coldB0ForwardControl
}

// A retained forward intent cannot fall back to the older component publication
// surface, even while its completion is absent. History is immutable, and this
// check shares the publication effect's exclusive transaction barriers.
func attestColdForwardPublication(ctx context.Context, tx pgx.Tx, c *Client, intent string, p *coldPublication, target *ColdB0Target, approval *coldForwardPublicationApproval) error {
	if approval != nil {
		return approval.attest(ctx, tx, c, intent, p, target)
	}
	var retained bool
	if err := tx.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM public.crawler_ownership_b0_forward WHERE intent_sha256=$1)", intent).Scan(&retained); err != nil {
		return err
	}
	if retained {
		return ErrAuthorityLost
	}
	return nil
}

func (a *coldForwardPublicationApproval) attest(ctx context.Context, tx pgx.Tx, c *Client, intent string, p *coldPublication, target *ColdB0Target) error {
	if a == nil || a.control == nil || !ownershipSHA256.MatchString(a.binding.PlanSHA256) || !ownershipSHA256.MatchString(a.binding.ReceiptSHA256) {
		return ErrConfiguration
	}
	state, err := loadColdB0ForwardApplication(ctx, tx, a.binding.PlanSHA256, p.plan.SourceRevision())
	if err != nil {
		return err
	}
	if state.receipt == nil || state.digest != a.binding.ReceiptSHA256 || state.plan.Request() != (ColdB0ForwardRequest{intent, p.plan.SourceRevision(), p.plan.Epoch(), p.plan.digest}) || state.plan.document.TargetSHA256 != target.digest {
		return ErrAuthorityLost
	}
	// Once active, legitimate workers may have advanced canonical schedules and
	// task lifecycle. Historical transfer completion remains immutable; current
	// routing/owner/cohort audits are supplied by the publication primitive.
	if p.phase == "active" {
		return nil
	}
	rows, hash, err := coldB0ForwardRows(ctx, tx, target)
	if err != nil {
		return err
	}
	if hash != state.plan.document.PostgresSHA256 || !reflect.DeepEqual(rows, state.plan.document.PostgresRows) {
		return ErrAuthorityLost
	}
	// Pending publication can precede the SQL phase commit; published Redis
	// bytes can likewise precede the published SQL commit. Observe only these
	// exact known pairs. Missing/expired/partial witnesses never get rebuilt.
	type witness struct{ projection, marker string }
	pending := publicationMarker(intent, p.spec, p.plan, "pending")
	published := publicationMarker(intent, p.spec, p.plan, "published")
	var witnesses []witness
	switch p.phase {
	case "reserved":
		witnesses = []witness{{p.previous, p.previousMarker}, {p.previous, pending}}
	case "publishing":
		witnesses = []witness{{p.previous, pending}, {p.plan.body, published}}
	case "published":
		witnesses = []witness{{p.plan.body, published}}
	default:
		return ErrAuthorityLost
	}
	for _, expected := range witnesses {
		observation := *p
		observation.previous, observation.previousMarker = expected.projection, expected.marker
		snapshot, hash, err := observeColdB0Forward(ctx, c, target, &observation, rows)
		if err != nil {
			if errors.Is(err, ErrAuthorityLost) {
				continue
			}
			return err
		}
		if hash != state.receipt.SnapshotSHA256 {
			return ErrAuthorityLost
		}
		return attestColdForwardManifest(ctx, a.control, target, snapshot)
	}
	return ErrAuthorityLost
}

// The full cold forward path requires exact retained transfer completion under
// the same exclusive barriers as each publication effect. Public entry points
// use only the fixed authenticated producer; private adapters are test-scoped.
func PrepareColdForwardOwnershipPublication(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, intent, revision string, target *ColdB0Target, binding ColdB0ForwardCompletionBinding) error {
	if control == nil {
		return ErrConfiguration
	}
	return prepareColdOwnershipPublication(ctx, pool, c, intent, revision, target, &coldForwardPublicationApproval{binding, control})
}

func PublishColdForwardOwnership(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, intent, revision string, target *ColdB0Target, binding ColdB0ForwardCompletionBinding) (*OwnershipPlan, error) {
	if control == nil {
		return nil, ErrConfiguration
	}
	return publishColdOwnership(ctx, pool, c, intent, revision, target, &coldForwardPublicationApproval{binding, control})
}

func ActivateColdForwardOwnership(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, intent, revision string, target *ColdB0Target, binding ColdB0ForwardCompletionBinding) (*OwnershipPlan, error) {
	if control == nil {
		return nil, ErrConfiguration
	}
	return activateColdOwnership(ctx, pool, c, intent, revision, target, &coldForwardPublicationApproval{binding, control})
}
