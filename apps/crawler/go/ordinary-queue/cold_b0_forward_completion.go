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

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0producer"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed cold_b0_forward_completion.sql
var coldB0ForwardCompletionSchema string

type coldB0ForwardReceipt struct {
	Version            string                 `json:"version"`
	PlanSHA256         string                 `json:"plan_sha256"`
	Request            ColdB0ForwardRequest   `json:"request"`
	TargetSHA256       string                 `json:"target_sha256"`
	SnapshotSHA256     string                 `json:"snapshot_sha256"`
	Snapshot           *coldB0ForwardSnapshot `json:"snapshot"`
	ProjectedOccupancy int64                  `json:"projected_lifetime_occupancy"`
}

type ColdB0ForwardApplication struct {
	plan         *ColdB0ForwardPlan
	receipt      *coldB0ForwardReceipt
	body, digest string
}

func (s *ColdB0ForwardApplication) Plan() *ColdB0ForwardPlan { return s.plan }
func (s *ColdB0ForwardApplication) Phase() string {
	if s.receipt == nil {
		return "prepared"
	}
	return "redis-transferred"
}
func (s *ColdB0ForwardApplication) ReceiptSHA256() string  { return s.digest }
func (s *ColdB0ForwardApplication) ReceiptPayload() string { return s.body }

func decodeColdB0ForwardReceipt(plan *ColdB0ForwardPlan, body, digest string) (*ColdB0ForwardApplication, error) {
	h := sha256.Sum256([]byte(body))
	if plan == nil || len(body) < 1 || len(body) > 32*1024*1024 || !ownershipSHA256.MatchString(digest) || hex.EncodeToString(h[:]) != digest {
		return nil, ErrAuthorityLost
	}
	var receipt coldB0ForwardReceipt
	d := json.NewDecoder(bytes.NewBufferString(body))
	d.DisallowUnknownFields()
	if d.Decode(&receipt) != nil || d.Decode(new(any)) != io.EOF || receipt.Version != "jobseek.crawler.cold-b0-forward-application/v1" || receipt.PlanSHA256 != plan.digest || receipt.Request != plan.Request() || receipt.TargetSHA256 != plan.document.TargetSHA256 || receipt.ProjectedOccupancy != plan.document.ProjectedOccupancy || receipt.SnapshotSHA256 != coldB0ForwardHash(receipt.Snapshot) {
		return nil, ErrAuthorityLost
	}
	done, err := classifyColdB0Forward(plan, receipt.Snapshot)
	if err != nil {
		return nil, ErrAuthorityLost
	}
	for _, finished := range done {
		if !finished {
			return nil, ErrAuthorityLost
		}
	}
	if int64(len(receipt.Snapshot.B0.Records)) != receipt.ProjectedOccupancy {
		return nil, ErrAuthorityLost
	}
	wanted, err := json.Marshal(receipt)
	if err != nil || !bytes.Equal(wanted, []byte(body)) {
		return nil, ErrAuthorityLost
	}
	return &ColdB0ForwardApplication{plan, &receipt, body, digest}, nil
}

func loadColdB0ForwardApplication(ctx context.Context, tx pgx.Tx, digest, revision string) (*ColdB0ForwardApplication, error) {
	plan, err := loadColdB0ForwardPlan(ctx, tx, digest, revision)
	if err != nil {
		return nil, err
	}
	var body, receiptSHA string
	err = tx.QueryRow(ctx, "SELECT payload,receipt_sha256 FROM public.crawler_ownership_b0_forward_completion WHERE plan_sha256=$1 AND source_revision=$2", digest, revision).Scan(&body, &receiptSHA)
	if errors.Is(err, pgx.ErrNoRows) {
		return &ColdB0ForwardApplication{plan: plan}, nil
	}
	if err != nil {
		return nil, err
	}
	return decodeColdB0ForwardReceipt(plan, body, receiptSHA)
}

func observeColdForwardApplication(ctx context.Context, tx pgx.Tx, c *Client, control coldB0ForwardControl, plan *ColdB0ForwardPlan, target *ColdB0Target) (*coldB0ForwardSnapshot, string, error) {
	if plan.document.TargetSHA256 != target.digest {
		return nil, "", ErrAuthorityLost
	}
	p, err := coldB0ForwardContext(ctx, tx, c, plan.Request(), target)
	if err != nil {
		return nil, "", err
	}
	rows, hash, err := coldB0ForwardRows(ctx, tx, target)
	if err != nil {
		return nil, "", err
	}
	if hash != plan.document.PostgresSHA256 {
		return nil, "", ErrAuthorityLost
	}
	snapshot, hash, err := observeColdB0Forward(ctx, c, target, p, rows)
	if err != nil {
		return nil, "", err
	}
	if err := attestColdForwardManifest(ctx, control, target, snapshot); err != nil {
		return nil, "", err
	}
	return snapshot, hash, nil
}

func applyRetainedColdB0Forward(ctx context.Context, pool *pgxpool.Pool, c *Client, control coldB0ForwardActivationControl, digest, revision string, target *ColdB0Target) (*ColdB0ForwardApplication, error) {
	var result *ColdB0ForwardApplication
	err := coldTransitionTransaction(ctx, pool, func(ctx context.Context, tx pgx.Tx) error {
		state, err := loadColdB0ForwardApplication(ctx, tx, digest, revision)
		if err != nil {
			return err
		}
		if state.receipt != nil {
			// Lost or changed persisted evidence never authorizes replay merely
			// because PostgreSQL retains an earlier completion receipt.
			_, hash, err := observeColdForwardApplication(ctx, tx, c, control, state.plan, target)
			if err != nil {
				return err
			}
			if hash != state.receipt.SnapshotSHA256 {
				return ErrAuthorityLost
			}
			result = state
			return nil
		}
		snapshot, err := applyColdB0Forward(ctx, tx, c, control, state.plan, target)
		if err != nil {
			return err
		}
		hash := coldB0ForwardHash(snapshot)
		if _, err := c.redis.Save(ctx).Result(); err != nil {
			return ErrObservation
		}
		_, readback, err := observeColdForwardApplication(ctx, tx, c, control, state.plan, target)
		if err != nil {
			return err
		}
		if readback != hash {
			return ErrAuthorityLost
		}
		receipt := coldB0ForwardReceipt{"jobseek.crawler.cold-b0-forward-application/v1", digest, state.plan.Request(), state.plan.document.TargetSHA256, hash, snapshot, state.plan.document.ProjectedOccupancy}
		body, err := json.Marshal(receipt)
		if err != nil || len(body) > 32*1024*1024 {
			return ErrProtocol
		}
		h := sha256.Sum256(body)
		receiptSHA := hex.EncodeToString(h[:])
		if _, err := decodeColdB0ForwardReceipt(state.plan, string(body), receiptSHA); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `INSERT INTO public.crawler_ownership_b0_forward_completion(plan_sha256,source_revision,receipt_sha256,payload)
 VALUES($1,$2,$3,$4)`, digest, revision, receiptSHA, string(body)); err != nil {
			return err
		}
		result, err = loadColdB0ForwardApplication(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

// InspectColdB0ForwardApplication observes immutable approval/completion history
// without allocator/lease locks, producer/Redis calls or release authority.
func InspectColdB0ForwardApplication(ctx context.Context, pool *pgxpool.Pool, digest, revision string) (*ColdB0ForwardApplication, error) {
	if ctx == nil || pool == nil || !ownershipSHA256.MatchString(digest) || !ownershipRevision.MatchString(revision) {
		return nil, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
	defer cancel()
	var result *ColdB0ForwardApplication
	err := pgx.BeginTxFunc(ctx, pool, pgx.TxOptions{AccessMode: pgx.ReadOnly}, func(tx pgx.Tx) error {
		if _, err := tx.Exec(ctx, "SET LOCAL statement_timeout='10s'"); err != nil {
			return err
		}
		var err error
		result, err = loadColdB0ForwardApplication(ctx, tx, digest, revision)
		return err
	})
	if err != nil {
		return nil, err
	}
	return result, nil
}

var _ coldB0ForwardActivationControl = (*b0producer.Client)(nil)
