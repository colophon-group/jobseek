package queue

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type coldOrdinaryPublicationReceipt struct {
	Version                string `json:"version"`
	PlanSHA256             string `json:"plan_sha256"`
	SourceRevision         string `json:"source_revision"`
	RetirementEpoch        int64  `json:"retirement_epoch"`
	B0ReceiptSHA256        string `json:"b0_receipt_sha256"`
	ProjectionSHA256       string `json:"projection_sha256"`
	MarkerSHA256           string `json:"marker_sha256"`
	CompatibleIntentSHA256 string `json:"compatible_intent_sha256"`
}
type coldOrdinaryCompletionReceipt struct {
	Version                  string `json:"version"`
	PlanSHA256               string `json:"plan_sha256"`
	SourceRevision           string `json:"source_revision"`
	RetirementEpoch          int64  `json:"retirement_epoch"`
	PublicationReceiptSHA256 string `json:"publication_receipt_sha256"`
	B0ReceiptSHA256          string `json:"b0_receipt_sha256"`
	ReversalSHA256           string `json:"reversal_sha256"`
	FreshOrdinaryPlanSHA256  string `json:"fresh_ordinary_plan_sha256"`
	CompatibleIntentSHA256   string `json:"compatible_intent_sha256"`
}
type ColdOrdinaryFinalizationApplication struct {
	plan                               *ColdOrdinaryFinalizationPlan
	phase                              string
	publicationBody, publicationDigest string
	body, digest                       string
}

func (s *ColdOrdinaryFinalizationApplication) Plan() *ColdOrdinaryFinalizationPlan { return s.plan }
func (s *ColdOrdinaryFinalizationApplication) Phase() string                       { return s.phase }
func (s *ColdOrdinaryFinalizationApplication) ReceiptSHA256() string               { return s.digest }
func (s *ColdOrdinaryFinalizationApplication) ReceiptPayload() string              { return s.body }
func (s *ColdOrdinaryFinalizationApplication) PublicationReceiptSHA256() string {
	return s.publicationDigest
}

func finalizationProjection(p *ColdOrdinaryFinalizationPlan, ordinary *ColdOrdinaryRestorationPlan) string {
	if p.compatible == nil {
		return ""
	}
	return ordinary.fresh.body
}
func finalizationMarker(p *ColdOrdinaryFinalizationPlan, ordinary *ColdOrdinaryRestorationPlan, state string) string {
	if p.compatible != nil {
		return publicationMarker(p.document.CompatibleIntentSHA256, *p.compatible, ordinary.fresh, state)
	}
	body, _ := json.Marshal(struct {
		Version         string `json:"version"`
		State           string `json:"state"`
		PlanSHA256      string `json:"plan_sha256"`
		RetirementEpoch int64  `json:"retirement_epoch"`
	}{"jobseek.crawler.cold-ordinary-legacy-publication/v1", state, p.digest, p.RetirementEpoch()})
	return string(body)
}
func finalizationPairAllowed(p *ColdOrdinaryFinalizationPlan, ordinary *ColdOrdinaryRestorationPlan, phase, projection, marker string) bool {
	prior := projection == p.document.PreviousProjection && marker == p.document.PreviousMarker
	pending := projection == p.document.PreviousProjection && marker == finalizationMarker(p, ordinary, "pending")
	published := projection == finalizationProjection(p, ordinary) && marker == finalizationMarker(p, ordinary, "published")
	switch phase {
	case "prepared":
		return prior || pending
	case "publishing":
		return pending || published
	case "published", "complete":
		return published
	}
	return false
}
func expectedFinalizationPublication(p *ColdOrdinaryFinalizationPlan, ordinary *ColdOrdinaryRestorationPlan) (string, string) {
	d := coldOrdinaryPublicationReceipt{"jobseek.crawler.cold-ordinary-publication/v1", p.digest, p.Request().SourceRevision, p.RetirementEpoch(), p.document.B0ReceiptSHA256, coldForwardBytesDigest(finalizationProjection(p, ordinary)), coldForwardBytesDigest(finalizationMarker(p, ordinary, "published")), p.document.CompatibleIntentSHA256}
	body, _ := json.Marshal(d)
	return string(body), coldForwardBytesDigest(string(body))
}
func expectedFinalizationCompletion(s *ColdOrdinaryFinalizationApplication) (string, string) {
	p := s.plan
	d := coldOrdinaryCompletionReceipt{"jobseek.crawler.cold-ordinary-completion/v1", p.digest, p.Request().SourceRevision, p.RetirementEpoch(), s.publicationDigest, p.document.B0ReceiptSHA256, p.document.ReversalSHA256, p.document.FreshOrdinaryPlanSHA256, p.document.CompatibleIntentSHA256}
	body, _ := json.Marshal(d)
	return string(body), coldForwardBytesDigest(string(body))
}
func loadColdOrdinaryFinalizationApplication(ctx context.Context, tx pgx.Tx, digest, source string) (*ColdOrdinaryFinalizationApplication, error) {
	p, err := loadColdOrdinaryFinalization(ctx, tx, digest, source)
	if err != nil {
		return nil, err
	}
	ordinary, err := loadColdOrdinaryRestoration(ctx, tx, p.Request().OrdinaryRestorationPlanSHA256, source)
	if err != nil {
		return nil, err
	}
	s := &ColdOrdinaryFinalizationApplication{plan: p, phase: "prepared"}
	var body, receipt *string
	err = tx.QueryRow(ctx, "SELECT phase,payload,receipt_sha256 FROM crawler_ownership_restoration_publication WHERE plan_sha256=$1", digest).Scan(&s.phase, &body, &receipt)
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return nil, err
	}
	if err == nil {
		switch s.phase {
		case "publishing":
			if body != nil || receipt != nil {
				return nil, ErrAuthorityLost
			}
		case "published":
			expected, sha := expectedFinalizationPublication(p, ordinary)
			if body == nil || receipt == nil || *body != expected || *receipt != sha {
				return nil, ErrAuthorityLost
			}
			s.publicationBody, s.publicationDigest = *body, *receipt
		default:
			return nil, ErrAuthorityLost
		}
	}
	var completionBody, completionSHA string
	err = tx.QueryRow(ctx, "SELECT payload,receipt_sha256 FROM crawler_ownership_restoration_completion WHERE plan_sha256=$1 AND source_revision=$2", digest, source).Scan(&completionBody, &completionSHA)
	if errors.Is(err, pgx.ErrNoRows) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	expected, sha := expectedFinalizationCompletion(s)
	if s.phase != "published" || completionBody != expected || completionSHA != sha {
		return nil, ErrAuthorityLost
	}
	s.phase, s.body, s.digest = "complete", completionBody, completionSHA
	return s, nil
}
func InspectColdOrdinaryFinalizationApplication(ctx context.Context, pool *pgxpool.Pool, digest, source string) (*ColdOrdinaryFinalizationApplication, error) {
	var result *ColdOrdinaryFinalizationApplication
	err := inspectColdB0Reactivation(ctx, pool, digest, source, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		return err
	})
	return result, err
}
func finalizationRedis(ctx context.Context, c *Client, p *ColdOrdinaryFinalizationPlan, ordinary *ColdOrdinaryRestorationPlan, target *ColdB0Target, operation string) (string, error) {
	if p.compatible == nil {
		operation += "-legacy"
	}
	keys := append(target.keys(), ownershipProjectionKey, coldPublicationKey)
	args := append(target.auditArguments(p.RetirementEpoch()), operation, p.document.PreviousProjection, finalizationProjection(p, ordinary), finalizationMarker(p, ordinary, "pending"), finalizationMarker(p, ordinary, "published"), p.document.PreviousMarker)
	script := "local function audited_b0()\n" + target.lua + "\nend\n" + coldOrdinaryFinalizationLua
	value, err := c.redis.Eval(ctx, script, keys, args...).Text()
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
func finalizationObserved(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, s *ColdOrdinaryFinalizationApplication, target *ColdB0Target) (*ColdOrdinaryRestorationPlan, error) {
	p := s.plan
	ordinary, state, b0, err := finalizationContext(ctx, tx, c, control, p.Request(), target, s.phase == "complete", p, s.phase)
	if err != nil {
		return nil, err
	}
	if p.document.ReversalSHA256 != state.digest || p.RetirementEpoch() != state.retirement || p.document.Mode != ordinary.Mode() || p.document.B0ReceiptSHA256 != b0.digest || p.document.TargetSHA256 != target.digest || p.document.FreshOrdinaryPlanSHA256 != ordinary.document.FreshOrdinaryPlanSHA256 {
		return nil, ErrAuthorityLost
	}
	if p.compatible != nil {
		if p.compatible.SourceRevision != ordinary.fresh.SourceRevision() || p.compatible.PreviousEpoch != ordinary.document.PreviousOwner.Epoch || p.compatible.PreviousOrdinaryPlanSHA256 != ordinary.document.PreviousOrdinaryPlanSHA256 || p.compatible.PreviousB0ReceiptSHA256 != state.spec.RollbackB0ReceiptSHA256 || p.compatible.TargetReleaseSHA256 != state.spec.RollbackReleaseSHA256 || p.compatible.RollbackReleaseSHA256 != state.spec.RollbackReleaseSHA256 {
			return nil, ErrAuthorityLost
		}
		if s.phase == "complete" {
			var body string
			if err := tx.QueryRow(ctx, `SELECT payload FROM crawler_ownership_transition WHERE intent_sha256=$1 AND source_revision=$2 AND routing_epoch=$3 AND reserved_plan_sha256=$4 AND phase='active'`, p.document.CompatibleIntentSHA256, ordinary.fresh.SourceRevision(), p.RetirementEpoch(), ordinary.fresh.digest).Scan(&body); err != nil || body != p.document.CompatiblePayload {
				return nil, ErrAuthorityLost
			}
		}
	}
	return ordinary, nil
}
func validFinalizationOperation(ctx context.Context, c *Client, control *b0producer.Client, digest, source string, target *ColdB0Target) bool {
	return ctx != nil && c != nil && control != nil && target != nil && ownershipSHA256.MatchString(digest) && ownershipRevision.MatchString(source)
}
func PrepareColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, digest, source string, target *ColdB0Target) error {
	if !validFinalizationOperation(ctx, c, control, digest, source, target) {
		return ErrConfiguration
	}
	return prepareColdOrdinaryFinalization(ctx, pool, c, control, digest, source, target)
}
func prepareColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, digest, source string, target *ColdB0Target) error {
	return coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		s, err := loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		if err != nil {
			return err
		}
		ordinary, err := finalizationObserved(ctx, tx, c, control, s, target)
		if err != nil {
			return err
		}
		operation := "inspect"
		if s.phase == "prepared" {
			operation = "prepare"
		} else if s.phase == "published" || s.phase == "complete" {
			operation = "inspect-published"
		}
		if _, err := finalizationRedis(ctx, c, s.plan, ordinary, target, operation); err != nil {
			return err
		}
		if s.phase == "prepared" {
			_, err = tx.Exec(ctx, "INSERT INTO crawler_ownership_restoration_publication(plan_sha256,source_revision) VALUES($1,$2)", digest, source)
		}
		return err
	})
}
func PublishColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, digest, source string, target *ColdB0Target) (*ColdOrdinaryFinalizationApplication, error) {
	if !validFinalizationOperation(ctx, c, control, digest, source, target) {
		return nil, ErrConfiguration
	}
	return publishColdOrdinaryFinalization(ctx, pool, c, control, digest, source, target)
}
func publishColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, digest, source string, target *ColdB0Target) (*ColdOrdinaryFinalizationApplication, error) {
	if err := prepareColdOrdinaryFinalization(ctx, pool, c, control, digest, source, target); err != nil {
		return nil, err
	}
	var result *ColdOrdinaryFinalizationApplication
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		s, err := loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		if err != nil {
			return err
		}
		ordinary, err := finalizationObserved(ctx, tx, c, control, s, target)
		if err != nil {
			return err
		}
		operation := "inspect-published"
		if s.phase == "publishing" {
			operation = "publish"
		}
		if _, err := finalizationRedis(ctx, c, s.plan, ordinary, target, operation); err != nil {
			return err
		}
		if s.phase == "publishing" {
			if reply, err := c.redis.Save(ctx).Result(); err != nil || reply != "OK" {
				return ErrObservation
			}
			// Reclassify the now-published bytes while retaining the unchanged B0 receipt.
			if _, err := finalizationObserved(ctx, tx, c, control, s, target); err != nil {
				return err
			}
			if _, err := finalizationRedis(ctx, c, s.plan, ordinary, target, "inspect-published"); err != nil {
				return err
			}
			body, sha := expectedFinalizationPublication(s.plan, ordinary)
			if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_restoration_publication SET phase='published',payload=$2,receipt_sha256=$3 WHERE plan_sha256=$1 AND phase='publishing'", digest, body, sha); err != nil {
				return err
			}
		}
		result, err = loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		return err
	})
	return result, err
}
func CompleteColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control *b0producer.Client, digest, source string, target *ColdB0Target) (*ColdOrdinaryFinalizationApplication, error) {
	if !validFinalizationOperation(ctx, c, control, digest, source, target) {
		return nil, ErrConfiguration
	}
	return completeColdOrdinaryFinalization(ctx, pool, c, control, digest, source, target)
}
func completeColdOrdinaryFinalization(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardControl, digest, source string, target *ColdB0Target) (*ColdOrdinaryFinalizationApplication, error) {
	var result *ColdOrdinaryFinalizationApplication
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		s, err := loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		if err != nil {
			return err
		}
		if s.phase != "published" && s.phase != "complete" {
			return ErrAuthorityLost
		}
		ordinary, err := finalizationObserved(ctx, tx, c, control, s, target)
		if err != nil {
			return err
		}
		if _, err := finalizationRedis(ctx, c, s.plan, ordinary, target, "inspect-published"); err != nil {
			return err
		}
		if s.phase == "complete" {
			result = s
			return nil
		}
		p := s.plan
		if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_reversal SET phase='complete' WHERE reversal_sha256=$1 AND phase='reserved'", p.document.ReversalSHA256); err != nil {
			return err
		}
		var original string
		if err := tx.QueryRow(ctx, "SELECT forward_intent_sha256 FROM crawler_ownership_reversal WHERE reversal_sha256=$1", p.document.ReversalSHA256).Scan(&original); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, "UPDATE crawler_ownership_transition SET phase='reversed' WHERE intent_sha256=$1 AND phase='reversing'", original); err != nil {
			return err
		}
		if p.compatible != nil {
			if _, err := tx.Exec(ctx, "UPDATE ordinary_worker_ownership_plan SET state='active' WHERE plan_sha256=$1 AND state='staged'", ordinary.fresh.digest); err != nil {
				return err
			}
			spec := p.compatible
			if _, err := tx.Exec(ctx, `INSERT INTO crawler_ownership_transition(intent_sha256,transition_id,source_revision,previous_epoch,prepared_plan_sha256,payload,phase,routing_epoch,reserved_plan_sha256) VALUES($1,$2,$3,$4,$5,$6,'active',$7,$8)`, p.document.CompatibleIntentSHA256, spec.TransitionID, spec.SourceRevision, spec.PreviousEpoch, spec.PreparedPlanSHA256, p.document.CompatiblePayload, p.RetirementEpoch(), ordinary.fresh.digest); err != nil {
				return err
			}
		}
		body, sha := expectedFinalizationCompletion(s)
		if _, err := tx.Exec(ctx, "INSERT INTO crawler_ownership_restoration_completion(plan_sha256,source_revision,receipt_sha256,payload) VALUES($1,$2,$3,$4)", digest, source, sha, body); err != nil {
			return err
		}
		result, err = loadColdOrdinaryFinalizationApplication(ctx, tx, digest, source)
		if err != nil {
			return err
		}
		if _, err := finalizationObserved(ctx, tx, c, control, result, target); err != nil {
			return err
		}
		return nil
	})
	return result, err
}
