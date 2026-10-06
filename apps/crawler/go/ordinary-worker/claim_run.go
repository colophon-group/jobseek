package worker

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	workday "github.com/colophon-group/jobseek/apps/crawler/go/workday-monitor"
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
	TaskKind                                             queue.Kind
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
func RunGreenhouseClaim(ctx context.Context, authority *queue.Authority, claim *queue.Claim, http *VerifiedDirectHTTP, preparer RichPreparer, circuits *queue.HostCircuits, renderers ...renderedMonitorClient) (*GreenhouseClaimResult, error) {
	result := &GreenhouseClaimResult{}
	if authority == nil || claim == nil || !claim.OwnershipBound() || http == nil || http.client == nil || preparer == nil || circuits == nil {
		return result, claimRunError("startup", queue.ErrConfiguration)
	}
	task := claim.Descriptor()
	if task.Kind != queue.Monitor || (task.Worker != queue.Simple && task.Worker != queue.Browser) {
		return result, claimRunError("startup", queue.ErrUnsupportedProfile)
	}
	profile, err := queue.InspectRichMonitor(task.ID, task.Config)
	if err != nil || queue.MonitorWorker(profile) != task.Worker {
		return result, claimRunError("startup", queue.ErrUnsupportedProfile)
	}
	var renderer renderedMonitorClient
	if len(renderers) > 0 {
		renderer = renderers[0]
	}
	if task.Worker == queue.Browser && renderer == nil {
		return result, claimRunError("startup", queue.ErrConfiguration)
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
		var terminal *queue.GreenhouseCycleResult
		var err error
		observed := observation.Snapshot()
		if task.Worker == queue.Browser && (observed.Responses == 0 || (observed.LastStatus != 401 && observed.LastStatus != 403 && observed.LastStatus != 429 && observed.LastStatus < 500)) {
			terminal, err = cycle.FinishFailure(ctx, message)
		} else {
			terminal, err = cycle.FinishFailureWithHostCircuit(ctx, message, preflight.Run, traffic())
		}
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
	var discovery RichDiscovery
	var fetchErr error
	var workdayReservation *workday.ReservationError
	var streamed *queue.GreenhouseInventorySummary
	if profile.Provider == "nextdata" {
		streamed = &queue.GreenhouseInventorySummary{}
		streamedIdentities := map[string]bool{}
		var renderedPage func(context.Context, string) nextdataPage
		if task.Worker == queue.Browser {
			provider, ok := renderer.(interface {
				FetchNextdataPage(context.Context, queue.GreenhouseMonitorProfile, map[string]string, string) nextdataPage
			})
			if !ok {
				return failure("configuration", queue.ErrUnsupportedProfile)
			}
			renderedPage = func(ctx context.Context, endpoint string) nextdataPage {
				return provider.FetchNextdataPage(ctx, profile, task.Config, endpoint)
			}
		}
		discovery, fetchErr = discoverNextdataWithPages(ctx, http.client, profile, task.Config, func(jobs []RichMonitorJob) error {
			inventory, err := NormalizeRichInventory(ctx, task.Config["board_url"], jobs, false)
			if err != nil {
				return err
			}
			for _, job := range inventory.Jobs {
				if job.SourceIdentity != "" {
					if streamedIdentities[job.SourceIdentity] {
						return errors.New("repeated explicit identity across streamed chunks")
					}
					streamedIdentities[job.SourceIdentity] = true
				}
			}
			streamed.Discovered += inventory.Discovered
			for _, count := range inventory.DropReasons {
				streamed.ProcessingFiltered += count
			}
			if profile.Profile == "nextdata.embedded-urls/v1" || profile.Profile == "nextdata.rendered-urls/v1" {
				for start := 0; start < len(inventory.Jobs); start += 500 {
					urls := []string{}
					for _, job := range inventory.Jobs[start:min(start+500, len(inventory.Jobs))] {
						urls = append(urls, job.URL)
					}
					batch, err := cycle.WriteURLOnlyBatch(ctx, urls)
					if err != nil {
						return err
					}
					result.Batches.Inserted += batch.Inserted
					result.Batches.Touched += batch.Touched
					result.Batches.Relisted += batch.Relisted
					result.Batches.Foreign += batch.Foreign
					result.Batches.ForeignRelisted += batch.ForeignRelisted
					result.Batches.Deduplicated += batch.Deduplicated
					for _, detail := range batch.Details {
						if _, err := cycle.EnqueueURLDetail(ctx, detail); err != nil {
							return err
						}
					}
				}
			} else {
				processed, err := WriteGreenhouseInventory(ctx, cycle, preparer, inventory)
				if processed != nil {
					batch := processed.Batches
					result.Batches.Inserted += batch.Inserted
					result.Batches.Touched += batch.Touched
					result.Batches.Relisted += batch.Relisted
					result.Batches.Foreign += batch.Foreign
					result.Batches.ForeignRelisted += batch.ForeignRelisted
					result.Batches.Deduplicated += batch.Deduplicated
				}
				if err != nil {
					return err
				}
			}
			return nil
		}, renderedPage)
	} else if profile.Provider == "paycom" {
		discovery, fetchErr = discoverPaycomInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "rippling" {
		discovery, fetchErr = discoverRipplingInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "comeet" {
		discovery, fetchErr = discoverComeetInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "jobvite" {
		discovery, fetchErr = discoverJobviteInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "softgarden" {
		discovery, fetchErr = discoverSoftgardenInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "ukg" {
		discovery, fetchErr = discoverUKGInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "bamboohr" {
		discovery, fetchErr = discoverBambooHRInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "recruiter_co_kr" {
		discovery, fetchErr = discoverRecruiterKRInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "mokahr" {
		discovery, fetchErr = discoverMokahrInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "almacareer" {
		discovery, fetchErr = discoverAlmaInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "eightfold" {
		discovery, fetchErr = discoverEightfoldInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "inline" {
		discovery, fetchErr = discoverInlineInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "beisen" {
		discovery, fetchErr = discoverBeisenInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "workday" {
		var metadata map[string]any
		if json.Unmarshal([]byte(task.Config["metadata"]), &metadata) != nil {
			return failure("configuration", queue.ErrConfiguration)
		}
		config, err := workday.ParseInventoryConfig(task.Config["board_url"], metadata)
		if err != nil {
			return failure("configuration", queue.ErrConfiguration)
		}
		inventory, err := DiscoverWorkdayInventory(ctx, http, config)
		fetchErr = err
		discovery.Truncated = inventory.Truncated
		for _, raw := range inventory.URLs {
			discovery.Jobs = append(discovery.Jobs, RichMonitorJob{URL: raw})
		}
		errors.As(err, &workdayReservation)
	} else if task.Worker == queue.Browser {
		discovery, fetchErr = renderer.FetchMonitor(ctx, profile, task.Config)
	} else if profile.Provider == "dom" {
		discovery, fetchErr = discoverDOMInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "phenom" {
		discovery, fetchErr = discoverPhenomInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "sitemap" {
		discovery, fetchErr = discoverSitemapInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "api_sniffer" {
		discovery, fetchErr = discoverAPISnifferInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "breezy" || profile.Provider == "gem" {
		discovery, fetchErr = discoverBreezyGemInventory(ctx, http.client, profile)
	} else if profile.Provider == "jobylon" {
		discovery, fetchErr = discoverJobylonInventory(ctx, http.client, profile)
	} else if profile.Provider == "gupy" {
		discovery, fetchErr = discoverGupyInventory(ctx, http.client, profile)
	} else if profile.Provider == "jazzhr" {
		discovery, fetchErr = discoverJazzHRInventory(ctx, http.client, profile)
	} else if profile.Provider == "icims" {
		discovery, fetchErr = discoverICIMSInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "oracle_hcm" {
		discovery, fetchErr = discoverOracleInventory(ctx, http.client, profile, task.Config)
	} else if profile.Provider == "join" {
		discovery, fetchErr = discoverJoinInventory(ctx, http.client, profile)
	} else if profile.Provider == "smartrecruiters" || profile.Provider == "workable" {
		discovery, fetchErr = discoverAPIInventory(ctx, http.client, profile, task.Config)
	} else {
		discovery, fetchErr = DiscoverRichMonitor(ctx, http.client, profile)
	}
	result.DiscoveryDuration = time.Since(started)
	result.DiscoveryError = fetchErr != nil
	result.DiscoveryCancelled = ctx.Err() != nil
	result.Discovered = len(discovery.Jobs)
	if ctx.Err() != nil {
		cycle.InvalidateInventory()
		return result, claimRunError("fetch", ctx.Err())
	}
	if reserved := workdayReservation; reserved != nil {
		policy := reserved.PolicyURL
		terminal, err := cycle.FinishReservationResource(ctx, reserved.URL, &queue.GreenhouseHeaderReservation{Endpoint: reserved.URL, PolicyURL: &policy})
		if err != nil {
			return result, claimRunError("reservation", err)
		}
		return finishSuccess(terminal)
	}
	if response := discovery.Response; response != nil {
		// These private fields come only from this sealed-client fetch. Bind the
		// initial resource to this claim's endpoint or validated page. RSS may
		// stop at a publisher header; incomplete inventories never reach writes.
		matches := richResponseMatches(profile, response.endpoint)
		if profile.Provider == "dom" {
			matches = queue.DOMMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if queue.SecondaryProvider(profile.Provider) {
			matches = queue.SecondaryMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "mokahr" || profile.Provider == "almacareer" || profile.Provider == "eightfold" {
			matches = queue.ProviderBatchMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "inline" {
			matches = queue.InlineMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "beisen" {
			matches = queue.BeisenMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "api_sniffer" {
			matches = queue.APISnifferMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "icims" {
			matches = queue.ICIMSMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "phenom" {
			matches = queue.PhenomMonitorResourceMatches(profile, response.endpoint)
		}
		if profile.Provider == "oracle_hcm" {
			matches = queue.OracleMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if profile.Provider == "nextdata" {
			matches = queue.NextdataMonitorResourceMatches(profile, task.Config, response.endpoint)
		}
		if !matches {
			cycle.InvalidateInventory()
			return result, claimRunError("response", queue.ErrConfiguration)
		}
		if response.reserved {
			initial := profile.Endpoint
			if queue.SecondaryProvider(profile.Provider) {
				initial = response.endpoint
			}
			if profile.Provider == "dom" || profile.Provider == "mokahr" || profile.Provider == "almacareer" || profile.Provider == "eightfold" || profile.Provider == "inline" || profile.Provider == "beisen" || profile.Provider == "api_sniffer" || profile.Provider == "smartrecruiters" || profile.Provider == "workable" || profile.Provider == "join" || profile.Provider == "sitemap" || profile.Provider == "oracle_hcm" || profile.Provider == "icims" || profile.Provider == "phenom" || profile.Provider == "nextdata" {
				initial = response.endpoint
			}
			terminal, err := cycle.FinishReservationResource(ctx, initial, &queue.GreenhouseHeaderReservation{Endpoint: response.finalURL, PolicyURL: response.PolicyURL(), Source: response.reservationSource})
			if err != nil {
				return result, claimRunError("reservation", err)
			}
			return finishSuccess(terminal)
		}
		providerGone := (profile.Provider != "dom" || response.endpoint == profile.Endpoint) && (response.status == 404 || (profile.Provider == "join" || profile.Provider == "dom" || profile.Provider == "jazzhr" || profile.Provider == "gupy") && response.status == 410) && profile.Provider != "api_sniffer" && profile.Provider != "pinpoint" && profile.Provider != "rss" && profile.Provider != "personio" && profile.Provider != "smartrecruiters" && profile.Provider != "workable" && profile.Provider != "sitemap" && profile.Provider != "oracle_hcm" && profile.Provider != "breezy" && profile.Provider != "gem" && profile.Provider != "phenom"
		if queue.SecondaryProvider(profile.Provider) {
			var failure *DiscoveryError
			providerGone = errors.As(fetchErr, &failure) && failure.Kind == "provider_gone" && queue.SecondaryMonitorGone(task.Config, response.endpoint, response.status, response.providerDisabled)
		}
		if profile.Provider == "mokahr" || profile.Provider == "almacareer" || profile.Provider == "eightfold" {
			var failure *DiscoveryError
			providerGone = errors.As(fetchErr, &failure) && failure.Kind == "provider_gone" && queue.ProviderBatchMonitorGone(task.Config, response.endpoint, response.status, response.providerDisabled)
		}
		if profile.Provider == "icims" {
			providerGone = queue.ICIMSMonitorPrimaryGone(task.Config, response.endpoint, response.status)
		}
		if profile.Provider == "nextdata" || profile.Provider == "inline" {
			providerGone = false
		}
		if profile.Provider == "beisen" {
			providerGone = queue.BeisenMonitorPrimaryGone(task.Config, response.endpoint, response.status, response.providerDisabled)
		}
		if providerGone {
			terminal, err := cycle.FinishProviderGoneResource(ctx, response.endpoint, queue.GreenhouseGoneObservation{Endpoint: response.finalURL, HTTPStatus: response.status, PortalDisabled: response.providerDisabled})
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
	if streamed != nil {
		streamed.Truncated = discovery.Truncated
		terminal, err := cycle.FinishSuccess(ctx, *streamed)
		if err != nil {
			return failure("lifecycle", err)
		}
		return finishSuccess(terminal)
	}
	if profile.Provider == "rss" || profile.Provider == "sitemap" || profile.Provider == "eightfold" || profile.Provider == "dom" {
		discovery.Jobs, err = applyFeedMonitorURLs(ctx, task.Config, discovery.Jobs)
		if err != nil {
			return failure("processing", err)
		}
	}
	inventory, err := NormalizeRichInventory(ctx, task.Config["board_url"], discovery.Jobs, discovery.Truncated)
	if err != nil {
		return failure("inventory", err)
	}
	inventory.MetadataUpdates = discovery.MetadataUpdates
	if profile.Provider == "workday" || profile.Provider == "smartrecruiters" || profile.Provider == "workable" || profile.Provider == "join" || profile.Provider == "sitemap" || profile.Provider == "dom" || profile.Provider == "icims" || profile.Provider == "breezy" || profile.Provider == "jazzhr" || profile.Provider == "gupy" || profile.Provider == "phenom" {
		for offset := 0; offset < len(inventory.Jobs); offset += 500 {
			end := min(offset+500, len(inventory.Jobs))
			urls := make([]string, 0, end-offset)
			for _, job := range inventory.Jobs[offset:end] {
				urls = append(urls, job.URL)
			}
			batch, err := cycle.WriteURLOnlyBatch(ctx, urls)
			if err != nil {
				return failure("processing", err)
			}
			result.Batches.Inserted += batch.Inserted
			result.Batches.Touched += batch.Touched
			result.Batches.Relisted += batch.Relisted
			result.Batches.Foreign += batch.Foreign
			result.Batches.ForeignRelisted += batch.ForeignRelisted
			result.Batches.Deduplicated += batch.Deduplicated
			for _, detail := range batch.Details {
				if _, err := cycle.EnqueueURLDetail(ctx, detail); err != nil {
					return failure("enqueue", err)
				}
			}
		}
		terminal, err := cycle.FinishSuccess(ctx, queue.GreenhouseInventorySummary{Discovered: inventory.Discovered, ProcessingFiltered: inventory.Discovered - len(inventory.Jobs), Truncated: inventory.Truncated})
		if err != nil {
			return failure("lifecycle", err)
		}
		return finishSuccess(terminal)
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
