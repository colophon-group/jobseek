package worker

import (
	"context"
	"errors"
	"log"
	"strings"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"github.com/jackc/pgx/v5/pgconn"
)

// ClaimRunError is safe to log. Upstream bodies/URLs, SQL messages and arbitrary
// preparation exceptions never enter its text; sentinel causes stay unwrap-able.
type ClaimRunError struct {
	Phase, Kind string
	cause       error
}

func (e *ClaimRunError) Error() string { return "ordinary rich monitor " + e.Phase + ": " + e.Kind }
func (e *ClaimRunError) Unwrap() error { return e.cause }

func claimRunError(phase string, err error) error {
	kind := "failed"
	switch {
	case errors.Is(err, context.Canceled):
		kind = "canceled"
	case errors.Is(err, context.DeadlineExceeded):
		kind = "deadline"
	case errors.Is(err, queue.ErrAuthorityLost):
		kind = "authority_lost"
	case errors.Is(err, queue.ErrConfiguration), errors.Is(err, queue.ErrUnsupportedProfile):
		kind = "configuration"
	case errors.Is(err, queue.ErrObservation), errors.Is(err, queue.ErrProtocol):
		kind = "unacknowledged"
	default:
		var database *pgconn.PgError
		if errors.As(err, &database) {
			switch database.Code {
			case "57014":
				kind = "query_cancelled"
			case "40P01":
				kind = "deadlock"
			case "55P03":
				kind = "lock_timeout"
			default:
				kind = "database"
			}
		}
	}
	return &ClaimRunError{Phase: phase, Kind: kind, cause: err}
}

type GreenhouseClaimResult struct {
	Cycle                                                *queue.GreenhouseCycleResult
	Batches                                              queue.GreenhouseRichBatchResult
	HTTP                                                 HTTPSnapshot
	Diagnostics                                          []string
	Settled                                              bool
	DiscoveryStarted, DiscoveryError, DiscoveryCancelled bool
	DiscoveryDuration                                    time.Duration
	Discovered                                           int
}

// RunGreenhouseClaim connects one already installed opaque claim to the native
// verified fetch, inventory, preparation, posting/lifecycle and host-circuit
// pipeline. It does not claim work or launch a process. The installed executable
// must supply task cancellation/heartbeat/drain, protected assets and identities.
// A result with an error is not a settled completion; a committed receipt may
// remain available for durable recovery without repeating network work.
func RunGreenhouseClaim(ctx context.Context, authority *queue.Authority, claim *queue.Claim, http *VerifiedDirectHTTP, preparer RichPreparer, circuits *queue.HostCircuits) (*GreenhouseClaimResult, error) {
	result := &GreenhouseClaimResult{}
	if authority == nil || claim == nil || !claim.OwnershipBound() || http == nil || http.client == nil || preparer == nil || circuits == nil {
		return result, claimRunError("startup", queue.ErrConfiguration)
	}
	task := claim.Descriptor()
	if task.Kind != queue.Monitor || task.Worker != queue.Simple {
		return result, claimRunError("startup", queue.ErrUnsupportedProfile)
	}
	profile, err := queue.InspectRichMonitor(task.ID, task.Config)
	if err != nil {
		return result, claimRunError("startup", err)
	}
	settle := func(cycle *queue.GreenhouseCycleResult) (*GreenhouseClaimResult, error) {
		result.Cycle = cycle
		if cycle == nil || cycle.Receipt == nil {
			return result, claimRunError("receipt", queue.ErrConfiguration)
		}
		if err := settleClaim(ctx, func() error { return authority.Settle(ctx, claim, cycle.Receipt) }); err != nil {
			return result, claimRunError("settlement", err)
		}
		result.Settled = true
		return result, nil
	}
	if receipt := claim.RecoveredReceipt(); receipt != nil {
		return settle(&queue.GreenhouseCycleResult{Receipt: receipt, Status: "recovered"})
	}
	preflight, err := authority.PreflightGreenhouseHost(ctx, claim, circuits)
	if err != nil {
		return result, claimRunError("preflight", err)
	}
	if preflight.Diagnostic != nil {
		result.Diagnostics = append(result.Diagnostics, "host_preflight_unavailable")
	}
	if preflight.Receipt != nil {
		return settle(&queue.GreenhouseCycleResult{Receipt: preflight.Receipt, Status: preflight.Status})
	}
	var observation *HTTPObservation
	traffic := func() queue.GreenhouseHostObservation {
		if observation == nil {
			return queue.GreenhouseHostObservation{}
		}
		snapshot := observation.Snapshot()
		return queue.GreenhouseHostObservation{Hosts: snapshot.Hosts, LastHost: snapshot.LastHost}
	}
	finishSuccess := func(terminal *queue.GreenhouseCycleResult) (*GreenhouseClaimResult, error) {
		result.Cycle = terminal
		if terminal == nil || terminal.Receipt == nil {
			return result, claimRunError("receipt", queue.ErrConfiguration)
		}
		if err := authority.RecordGreenhouseHostSuccess(ctx, preflight.Run, terminal.Receipt, traffic()); err != nil {
			if errors.Is(err, queue.ErrObservation) || errors.Is(err, queue.ErrProtocol) {
				result.Diagnostics = append(result.Diagnostics, "host_success_unavailable")
			} else {
				return result, claimRunError("host_success", err)
			}
		}
		return settle(terminal)
	}
	cycle, err := authority.BeginGreenhouseCycle(ctx, claim)
	if errors.Is(err, queue.ErrPublisherReserved) {
		terminal, err := authority.FinishGreenhouseReservation(ctx, claim, nil)
		if err != nil {
			return result, claimRunError("reservation", err)
		}
		return finishSuccess(terminal)
	}
	if err != nil {
		return result, claimRunError("cycle", err)
	}
	failure := func(phase string, cause error) (*GreenhouseClaimResult, error) {
		cycle.InvalidateInventory()
		if ctx.Err() != nil {
			return result, claimRunError(phase, ctx.Err())
		}
		if errors.Is(cause, queue.ErrAuthorityLost) {
			return result, claimRunError(phase, cause)
		}
		// A reservation can race native preparation or a chunk write. Keep it
		// monotonic and skip failure accounting under the fresh canonical lock.
		if errors.Is(cause, queue.ErrPublisherReserved) {
			terminal, err := cycle.FinishReservation(ctx, nil)
			if err != nil {
				return result, claimRunError("reservation", err)
			}
			return finishSuccess(terminal)
		}
		message := "native_" + phase + "_failed"
		if phase == "processing" {
			// The persisted aggregate remains compatible; log only a bounded
			// phase/category so preparation and SQL failures can be diagnosed
			// without exposing upstream content, credentials or SQL messages.
			var diagnostic *ClaimRunError
			if !errors.As(cause, &diagnostic) {
				diagnostic = claimRunError(phase, cause).(*ClaimRunError)
			}
			log.Print(diagnostic.Error())
		}
		if phase == "fetch" {
			var discoveryError *DiscoveryError
			if errors.As(cause, &discoveryError) {
				message = discoveryError.Error()
			}
		}
		terminal, err := cycle.FinishFailureWithHostCircuit(ctx, message, preflight.Run, traffic())
		if err != nil {
			return result, claimRunError("failure", err)
		}
		if terminal.HostCircuit != nil && terminal.HostCircuit.Diagnostic != nil {
			result.Diagnostics = append(result.Diagnostics, "host_failure_unavailable")
		}
		return settle(terminal)
	}
	ctx, observation = ObserveHTTP(ctx)
	defer func() { result.HTTP = observation.Snapshot() }()
	result.DiscoveryStarted = true
	started := time.Now()
	discovery, fetchErr := DiscoverRichMonitor(ctx, http.client, profile)
	result.DiscoveryDuration = time.Since(started)
	result.DiscoveryError = fetchErr != nil
	result.DiscoveryCancelled = ctx.Err() != nil
	result.Discovered = len(discovery.Jobs)
	if ctx.Err() != nil {
		cycle.InvalidateInventory()
		return result, claimRunError("fetch", ctx.Err())
	}
	if response := discovery.Response; response != nil {
		// These private fields come only from this completed sealed-client fetch.
		// The initial endpoint must still match the exact claim token. Redirect
		// headers, partial reads and caller-synthesized observations are excluded.
		if response.endpoint != profile.Endpoint && !(profile.Provider == "lever" && strings.HasPrefix(response.endpoint, strings.TrimSuffix(profile.Endpoint, "skip=0")+"skip=")) {
			cycle.InvalidateInventory()
			return result, claimRunError("response", queue.ErrConfiguration)
		}
		if response.reserved {
			terminal, err := cycle.FinishReservationResource(ctx, profile.Endpoint, &queue.GreenhouseHeaderReservation{Endpoint: response.finalURL, PolicyURL: response.PolicyURL()})
			if err != nil {
				return result, claimRunError("reservation", err)
			}
			return finishSuccess(terminal)
		}
		if response.status == 404 && profile.Provider != "pinpoint" {
			terminal, err := cycle.FinishProviderGoneResource(ctx, response.endpoint, queue.GreenhouseGoneObservation{Endpoint: response.finalURL, HTTPStatus: response.status})
			if err != nil {
				return result, claimRunError("provider_gone", err)
			}
			// Provider disappearance is not a generic host success or failure.
			return settle(terminal)
		}
	}
	if fetchErr != nil {
		return failure("fetch", fetchErr)
	}
	inventory, err := NormalizeRichInventory(ctx, task.Config["board_url"], discovery.Jobs, discovery.Truncated)
	if err != nil {
		return failure("inventory", err)
	}
	processed, err := PersistGreenhouseInventory(ctx, cycle, preparer, inventory)
	if processed != nil {
		result.Batches = processed.Batches
	}
	if err != nil {
		return failure("processing", err)
	}
	result.Batches = processed.Batches
	return finishSuccess(processed.Cycle)
}
