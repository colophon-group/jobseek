package queue

import (
	"context"
	"encoding/json"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// GreenhouseHeaderReservation represents a literal resource-level HTTP header
// opt-out checked before interpreting the API status or parsing its body.
// It records evidence; policy URLs do not authorize retrieval or other actions.
type GreenhouseHeaderReservation struct {
	Endpoint  string
	PolicyURL *string
}

// FinishGreenhouseReservation handles both a pre-existing durable reservation
// (nil observation, no fetch permitted) and a new exact-endpoint HTTP signal.
// The canonical future deadline and attempt receipt commit together. This
// closes the legacy Redis-only skip deadline gap without changing visibility,
// the success/failure budget, or granting permission when headers disappear.
func (a *Authority) FinishGreenhouseReservation(ctx context.Context, claim *Claim, observation *GreenhouseHeaderReservation) (*GreenhouseCycleResult, error) {
	initial := ""
	if observation != nil {
		initial = observation.Endpoint
	}
	return a.FinishGreenhouseReservationResource(ctx, claim, initial, observation)
}

// FinishGreenhouseReservationResource records the completed final resource
// while binding its original request to the installed token profile. A nil
// observation remains the pre-existing durable opt-out path with no fetch.
func (a *Authority) FinishGreenhouseReservationResource(ctx context.Context, claim *Claim, initialEndpoint string, observation *GreenhouseHeaderReservation) (*GreenhouseCycleResult, error) {
	if a == nil || !a.valid(claim) || a.ownership == nil || claim.task.Kind != Monitor || claim.recovered != nil {
		return nil, ErrConfiguration
	}
	profile, err := InspectRichMonitor(claim.task.ID, claim.task.Config)
	if err != nil {
		return nil, err
	}
	if observation != nil {
		if !initialMonitorResourceMatches(profile, initialEndpoint) || !validGreenhouseResponseResource(observation.Endpoint) {
			return nil, ErrConfiguration
		}
		if policy := observation.PolicyURL; policy != nil && (len(*policy) > 8192 || !utf8.ValidString(*policy) || strings.ContainsRune(*policy, 0)) {
			return nil, ErrConfiguration
		}
	}
	result := &GreenhouseCycleResult{Status: "publisher_reserved"}
	receipt, err := a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
		var reserved bool
		var now time.Time
		if err := tx.QueryRow(ctx, "SELECT tdm_reserved,now() FROM job_board WHERE id=$1::uuid", claim.task.ID).Scan(&reserved, &now); err != nil {
			return err
		}
		if observation == nil && !reserved {
			return ErrConfiguration
		}
		if observation != nil {
			evidence, err := json.Marshal(map[string]any{"url": observation.Endpoint, "source": "header", "policy_url": observation.PolicyURL, "observed_at": now.UTC().Format(time.RFC3339Nano)})
			if err != nil {
				return ErrConfiguration
			}
			// The board's existing monotonic propagation trigger also marks
			// retained/concurrent postings, preserving their public visibility.
			if _, err := tx.Exec(ctx, "UPDATE job_board SET tdm_reserved=true,tdm_reservation=$2::jsonb WHERE id=$1::uuid", claim.task.ID, string(evidence)); err != nil {
				return err
			}
		}
		_, err := tx.Exec(ctx, `UPDATE job_board SET next_check_at=now()+(check_interval_minutes || ' minutes')::interval,
 lease_owner=NULL,leased_until=NULL,updated_at=now() WHERE id=$1::uuid`, claim.task.ID)
		return err
	})
	if err != nil {
		return nil, err
	}
	result.Receipt = receipt
	receipt.terminalOutcome = "publisher_reserved"
	return result, nil
}

func (c *GreenhouseCycle) FinishReservation(ctx context.Context, observation *GreenhouseHeaderReservation) (*GreenhouseCycleResult, error) {
	initial := ""
	if observation != nil {
		initial = observation.Endpoint
	}
	return c.FinishReservationResource(ctx, initial, observation)
}

func (c *GreenhouseCycle) FinishReservationResource(ctx context.Context, initialEndpoint string, observation *GreenhouseHeaderReservation) (*GreenhouseCycleResult, error) {
	if c == nil || c.authority == nil {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done {
		return nil, ErrConfiguration
	}
	result, err := c.authority.FinishGreenhouseReservationResource(ctx, c.claim, initialEndpoint, observation)
	if err == nil {
		c.done = true
	}
	return result, err
}
