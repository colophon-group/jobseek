package queue

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type coldB0ReactivationReceiptDocument struct {
	Version    string               `json:"version"`
	PlanSHA256 string               `json:"plan_sha256"`
	Receipt    coldB0ForwardReceipt `json:"receipt"`
}
type ColdB0ReactivationApplication struct {
	plan         *ColdB0ReactivationPlan
	application  *ColdB0ForwardApplication
	body, digest string
}

func (s *ColdB0ReactivationApplication) Plan() *ColdB0ReactivationPlan { return s.plan }
func (s *ColdB0ReactivationApplication) Phase() string {
	if s.application == nil {
		return "prepared"
	}
	return "redis-reactivated"
}
func (s *ColdB0ReactivationApplication) ReceiptSHA256() string  { return s.digest }
func (s *ColdB0ReactivationApplication) ReceiptPayload() string { return s.body }

func decodeColdB0ReactivationApplication(plan *ColdB0ReactivationPlan, body, digest string) (*ColdB0ReactivationApplication, error) {
	if plan == nil || len(body) < 1 || len(body) > 32*1024*1024 || !ownershipSHA256.MatchString(digest) || coldForwardBytesDigest(body) != digest {
		return nil, ErrAuthorityLost
	}
	var doc coldB0ReactivationReceiptDocument
	d := json.NewDecoder(strings.NewReader(body))
	d.DisallowUnknownFields()
	if d.Decode(&doc) != nil || d.Decode(new(any)) != io.EOF || doc.Version != "jobseek.crawler.cold-b0-reactivation-application/v1" || doc.PlanSHA256 != plan.digest {
		return nil, ErrAuthorityLost
	}
	receiptBody, err := json.Marshal(doc.Receipt)
	if err != nil {
		return nil, ErrAuthorityLost
	}
	application, err := decodeColdB0ForwardReceipt(plan.forward, string(receiptBody), coldB0ForwardHash(doc.Receipt))
	if err != nil {
		return nil, err
	}
	canonical, err := json.Marshal(doc)
	if err != nil || !bytes.Equal(canonical, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return &ColdB0ReactivationApplication{plan, application, body, digest}, nil
}
func coldForwardBytesDigest(body string) string {
	// This helper hashes bytes, not JSON string encoding.
	h := sha256.Sum256([]byte(body))
	return hex.EncodeToString(h[:])
}
func loadColdB0ReactivationApplication(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdB0ReactivationApplication, error) {
	plan, err := loadColdB0ReactivationPlan(ctx, tx, digest, revision)
	if err != nil {
		return nil, err
	}
	var body, receipt string
	err = tx.QueryRow(ctx, "SELECT payload,receipt_sha256 FROM crawler_ownership_b0_reactivation_completion WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body, &receipt)
	if errors.Is(err, pgx.ErrNoRows) {
		return &ColdB0ReactivationApplication{plan: plan}, nil
	}
	if err != nil {
		return nil, err
	}
	return decodeColdB0ReactivationApplication(plan, body, receipt)
}
func InspectColdB0ReactivationApplication(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdB0ReactivationApplication, error) {
	var result *ColdB0ReactivationApplication
	err := inspectColdB0Reactivation(ctx, pool, digest, revision, func(ctx context.Context, tx pgx.Tx) error {
		var err error
		result, err = loadColdB0ReactivationApplication(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}
