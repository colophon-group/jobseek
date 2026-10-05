package queue

import (
	"context"
	"fmt"
	"math"
	"time"

	"github.com/jackc/pgx/v5"
)

type greenhouseGoneState struct {
	Status                     string
	Count                      int
	First, Last, Success, Gone *time.Time
}

type greenhouseGoneDecision struct {
	Status             string
	Count, Required    int
	Advanced, Terminal bool
	First, Last, Due   time.Time
	Gone               *time.Time
}

// Native Greenhouse's token-specific API 404 is a provider disappearance
// signal. Generic network, parsing, HTTP failures and partial inventories must
// never enter this path. The cycle verifies the exact endpoint and status.
type GreenhouseGoneObservation struct {
	Endpoint   string
	HTTPStatus int
}

func evaluateGreenhouseGone(state greenhouseGoneState, now time.Time) (greenhouseGoneDecision, error) {
	count := state.Count
	if count < 0 {
		count = 0
	}
	required := 2
	if state.Success != nil && !state.Success.Before(now.Add(-7*24*time.Hour)) {
		required = 3
	}
	decision := greenhouseGoneDecision{Required: required, Count: count}
	if state.Status == "gone" {
		decision.Status = "gone"
		decision.Advanced = state.Last == nil || now.Sub(*state.Last) >= 24*time.Hour
		decision.Last = now
		if !decision.Advanced {
			decision.Last = *state.Last
		}
		decision.First = decision.Last
		if state.First != nil {
			decision.First = *state.First
		}
		gone := decision.Last
		if state.Gone != nil {
			gone = *state.Gone
		}
		decision.Gone = &gone
		decision.Due = decision.Last.Add(24 * time.Hour)
		return decision, nil
	}
	decision.Advanced = count == 0 || state.Last == nil || now.Sub(*state.Last) >= 6*time.Hour
	if decision.Advanced {
		if count == math.MaxInt32 {
			return greenhouseGoneDecision{}, ErrConfiguration
		}
		decision.Count++
	}
	decision.Last = now
	if !decision.Advanced {
		decision.Last = *state.Last
	}
	decision.First = decision.Last
	if decision.Advanced && count == 0 {
		decision.First = now
	} else if state.First != nil {
		decision.First = *state.First
	}
	decision.Terminal = decision.Count >= required
	decision.Status = "gone_pending"
	decision.Due = decision.Last.Add(6 * time.Hour)
	if decision.Terminal {
		decision.Status = "gone"
		decision.Gone = &now
		decision.Due = decision.Last.Add(24 * time.Hour)
	}
	return decision, nil
}

func (c *GreenhouseCycle) FinishProviderGone(ctx context.Context, observation GreenhouseGoneObservation) (*GreenhouseCycleResult, error) {
	return c.FinishProviderGoneResource(ctx, observation.Endpoint, observation)
}

// FinishProviderGoneResource retains the final response resource as evidence
// while checking the initial token endpoint against the exact claim profile.
// The worker must supply its own completed verified response, never a URL from
// an inventory posting, redirect header, probe or caller-synthesized result.
func (c *GreenhouseCycle) FinishProviderGoneResource(ctx context.Context, initialEndpoint string, observation GreenhouseGoneObservation) (*GreenhouseCycleResult, error) {
	if c == nil || c.authority == nil || (observation.HTTPStatus != 404 && observation.HTTPStatus != 410) {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done || c.failed || c.processed != 0 {
		return nil, ErrConfiguration
	}
	profile, err := InspectRichMonitor(c.claim.task.ID, c.claim.task.Config)
	if err != nil || initialEndpoint != profile.Endpoint || observation.HTTPStatus == 410 && profile.Provider != "join" && profile.Provider != "dom" || !validGreenhouseResponseResource(observation.Endpoint) {
		return nil, ErrConfiguration
	}
	result := &GreenhouseCycleResult{}
	receipt, err := c.authority.Write(ctx, c.claim, true, func(ctx context.Context, tx pgx.Tx) error {
		if _, err := c.metadata(ctx, tx, true); err != nil {
			return err
		}
		var state greenhouseGoneState
		if err := tx.QueryRow(ctx, lifecycleQuery("gone_state"), c.claim.task.ID).Scan(&state.Status, &state.Count, &state.First, &state.Last, &state.Success, &state.Gone); err != nil {
			return err
		}
		var now time.Time
		if err := tx.QueryRow(ctx, "SELECT now()").Scan(&now); err != nil {
			return err
		}
		decision, err := evaluateGreenhouseGone(state, now)
		if err != nil {
			return err
		}
		var status string
		var count int
		var due time.Time
		provider := map[string]string{"greenhouse": "Greenhouse", "ashby": "Ashby", "lever": "Lever", "recruitee": "Recruitee", "join": "JOIN"}[profile.Provider]
		if err := tx.QueryRow(ctx, lifecycleQuery("gone"), c.claim.task.ID, decision.Status, decision.Count,
			decision.First, decision.Last, decision.Gone, decision.Due, fmt.Sprintf("%s API returned HTTP %d", provider, observation.HTTPStatus), observation.Endpoint, observation.HTTPStatus, decision.Terminal).Scan(&status, &count, &due); err != nil {
			return err
		}
		result.Status = decision.Status
		if decision.Terminal {
			result.Gone, err = c.countReturned(ctx, tx, "delist", c.claim.task.ID)
		}
		return err
	})
	if err != nil {
		return nil, err
	}
	c.done, result.Receipt = true, receipt
	return result, nil
}
