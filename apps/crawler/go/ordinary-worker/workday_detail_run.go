package worker

import (
	"context"
	"errors"
	"strings"
	"time"
	"unicode/utf8"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	publisherpolicy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
	"github.com/jackc/pgx/v5"
)

// RunWorkdayDetail executes one existing owned attempt with the shared native
// detail processor and SQL writer. Heartbeat, cancellation and shutdown remain
// the enclosing runtime's responsibility; recovery never repeats an origin GET.
func RunWorkdayDetail(ctx context.Context, authority *queue.Authority, claim *queue.Claim, http *VerifiedDirectHTTP, processor *executor.Processor, circuits *queue.HostCircuits) (*GreenhouseClaimResult, error) {
	result := &GreenhouseClaimResult{TaskKind: queue.Scrape}
	if authority == nil || claim == nil || !claim.OwnershipBound() || http == nil || http.client == nil || processor == nil || circuits == nil {
		return result, claimRunError("detail_startup", queue.ErrConfiguration)
	}
	task := claim.Descriptor()
	if task.Kind != queue.Scrape || task.Worker != queue.Simple {
		return result, claimRunError("detail_startup", queue.ErrUnsupportedProfile)
	}
	settle := func(receipt *queue.Receipt, status string) (*GreenhouseClaimResult, error) {
		result.Cycle = &queue.GreenhouseCycleResult{Receipt: receipt, Status: status}
		if receipt == nil {
			return result, claimRunError("detail_receipt", queue.ErrConfiguration)
		}
		if err := settleClaim(ctx, func() error { return authority.Settle(ctx, claim, receipt) }); err != nil {
			return result, claimRunError("detail_settlement", err)
		}
		result.Settled = true
		return result, nil
	}
	if receipt := claim.RecoveredReceipt(); receipt != nil {
		return settle(receipt, "recovered")
	}
	detail, err := authority.ReadWorkdayDetail(ctx, claim)
	if err != nil {
		return result, claimRunError("detail_read", err)
	}
	var preflight *queue.GreenhouseHostPreflight
	var observation *HTTPObservation
	var hostFailure, hostReachable bool
	traffic := func() queue.GreenhouseHostObservation {
		if observation == nil {
			return queue.GreenhouseHostObservation{}
		}
		snapshot := observation.Snapshot()
		return queue.GreenhouseHostObservation{Hosts: snapshot.Hosts, LastHost: snapshot.LastHost}
	}
	finish := func(cause error, reservation *publisherpolicy.Reservation) (*GreenhouseClaimResult, error) {
		if ctx.Err() != nil {
			return result, claimRunError("detail_execution", ctx.Err())
		}
		if errors.Is(cause, queue.ErrAuthorityLost) || errors.Is(cause, queue.ErrConfiguration) {
			return result, claimRunError("detail_execution", cause)
		}
		var run *queue.GreenhouseHostRun
		if cause != nil && reservation == nil && hostFailure && preflight != nil {
			run = preflight.Run
		}
		status := ""
		receipt, err := authority.FinishWorkdayDetail(ctx, detail, run, traffic(), func(ctx context.Context, tx pgx.Tx, current *queue.CurrentWorkdayDetail, circuit *queue.HostCircuitOutcome) (string, error) {
			// A positive opt-out stays durable even if a concurrent monitor
			// removed the posting while its HTTP response was arriving.
			if reservation != nil {
				if err := executor.RecordReservation(ctx, tx, current.PostingID(), reservation); err != nil {
					return "", err
				}
			}
			if !current.Schedulable {
				_, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=NULL,leased_until=NULL WHERE id=$1::uuid", current.PostingID())
				status = "unscheduled"
				return status, err
			}
			if current.PublisherReserved || reservation != nil || errors.Is(cause, queue.ErrPublisherReserved) {
				_, err := tx.Exec(ctx, `UPDATE job_posting p SET next_scrape_at=now()+(b.scrape_interval_hours||' hours')::interval,leased_until=NULL
 FROM job_board b WHERE p.id=$1::uuid AND b.id=p.board_id`, current.PostingID())
				status = "publisher_reserved"
				return status, err
			}
			if cause == nil {
				return "", queue.ErrConfiguration
			}
			if err := executor.RecordFailure(ctx, tx, current.PostingID(), executor.FailureClass(cause)); err != nil {
				return "", err
			}
			if circuit != nil && circuit.OpenUntil != nil {
				if _, err := tx.Exec(ctx, "UPDATE job_posting SET next_scrape_at=GREATEST(next_scrape_at,$2::timestamptz) WHERE id=$1::uuid AND is_active AND next_scrape_at IS NOT NULL", current.PostingID(), *circuit.OpenUntil); err != nil {
					return "", err
				}
			}
			status = "failed"
			return status, nil
		})
		if err != nil {
			return result, claimRunError("detail_terminal", err)
		}
		if hostReachable && !hostFailure {
			if err := authority.RecordGreenhouseHostSuccess(ctx, preflight.Run, receipt, traffic()); err != nil {
				if errors.Is(err, queue.ErrObservation) || errors.Is(err, queue.ErrProtocol) {
					result.Diagnostics = append(result.Diagnostics, "host_success_unavailable")
				} else {
					return result, claimRunError("detail_host_success", err)
				}
			}
		}
		return settle(receipt, status)
	}
	if detail.PublisherReserved || !detail.Schedulable {
		return finish(nil, nil)
	}
	preflight, err = authority.PreflightGreenhouseHost(ctx, claim, circuits)
	if err != nil {
		return result, claimRunError("detail_preflight", err)
	}
	if preflight.Diagnostic != nil {
		result.Diagnostics = append(result.Diagnostics, "host_preflight_unavailable")
	}
	if preflight.Receipt != nil {
		return settle(preflight.Receipt, preflight.Status)
	}
	detail, err = authority.BeginWorkdayDetail(ctx, claim)
	if err != nil {
		return result, claimRunError("detail_begin", err)
	}
	if detail.PublisherReserved || !detail.Schedulable {
		return finish(nil, nil)
	}
	ctx, observation = ObserveHTTP(ctx)
	defer func() { result.HTTP = observation.Snapshot() }()
	started := time.Now()
	result.DiscoveryStarted = true
	profile := detail.Profile()
	fetched, err := FetchWorkdayDetail(ctx, http, profile.SourceURL, profile.FacilityTenantAliases)
	result.DiscoveryDuration = time.Since(started)
	// Scrape failure budgets and shared host reachability are independent:
	// reachable empty/invalid extraction must not block every job on the host.
	hostReachable = fetched.Responses > 0
	hostFailure = fetched.TransportErrors > 0 || fetched.Status == 401 || fetched.Status == 403 || fetched.Status == 429 || fetched.Status >= 500
	var fetchFailure *workday.DetailFetchError
	if errors.As(err, &fetchFailure) && (fetchFailure.Kind == "transport" || fetchFailure.Kind == "invalid_payload") {
		hostFailure = true
	}
	if err != nil {
		result.DiscoveryError = true
		result.DiscoveryCancelled = ctx.Err() != nil
		var reserved *workday.ReservationError
		if errors.As(err, &reserved) {
			var policy *string
			if len(reserved.PolicyURL) > 8192 || !utf8.ValidString(reserved.PolicyURL) || strings.ContainsRune(reserved.PolicyURL, 0) {
				// Policy links are optional evidence. Their malformed value
				// must not discard the resource's explicit mining opt-out.
				result.Diagnostics = append(result.Diagnostics, "invalid_policy_url")
			} else if reserved.PolicyURL != "" {
				policy = &reserved.PolicyURL
			}
			return finish(nil, &publisherpolicy.Reservation{URL: reserved.URL, Source: "header", PolicyURL: policy})
		}
		var failed *workday.DetailFetchError
		if errors.As(err, &failed) && failed.Kind == "http" {
			err = &executor.NavigationHTTPError{RequestedURL: profile.Endpoint, ResponseURL: profile.Endpoint, Status: uint32(failed.Status)}
		}
		return finish(err, nil)
	}
	// Workday 404/S22 returns an empty JobContent in the existing scraper.
	// Keep its transient backoff rather than inventing immediate delisting.
	if fetched.Gone {
		return finish(executor.ErrEmptyResult, nil)
	}
	result.Discovered = 1
	receipt, err := PersistWorkdayDetail(ctx, authority, detail, processor, fetched.Content)
	if err != nil {
		return finish(err, nil)
	}
	if err := authority.RecordGreenhouseHostSuccess(ctx, preflight.Run, receipt, traffic()); err != nil {
		if errors.Is(err, queue.ErrObservation) || errors.Is(err, queue.ErrProtocol) {
			result.Diagnostics = append(result.Diagnostics, "host_success_unavailable")
		} else {
			return result, claimRunError("detail_host_success", err)
		}
	}
	return settle(receipt, "succeeded")
}
