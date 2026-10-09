package worker

import (
	"context"
	"encoding/json"
	"errors"
	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"log"
	"net/http"
	"net/http/cookiejar"
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
	return RunDetail(ctx, authority, claim, http, processor, circuits)
}

func RunDetail(ctx context.Context, authority *queue.Authority, claim *queue.Claim, http *VerifiedDirectHTTP, processor *executor.Processor, circuits *queue.HostCircuits, renderers ...renderedDetailClient) (*GreenhouseClaimResult, error) {
	result := &GreenhouseClaimResult{TaskKind: queue.Scrape}
	if authority == nil || claim == nil || !claim.OwnershipBound() || http == nil || http.client == nil || http.skipSSL || processor == nil || circuits == nil {
		return result, claimRunError("detail_startup", queue.ErrConfiguration)
	}
	task := claim.Descriptor()
	var renderer renderedDetailClient
	if len(renderers) == 1 {
		renderer = renderers[0]
	}
	if len(renderers) > 1 || task.Kind != queue.Scrape || (task.Worker != queue.Simple && task.Worker != queue.Browser) || (task.Worker == queue.Browser && renderer == nil) {
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
		if cause != nil {
			log.Print(claimRunError("detail_failure", cause))
		}
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
	if http.proxyRequired != queue.ProfileRequiresProxy(profile.Profile) {
		return result, claimRunError("detail_transport", queue.ErrConfiguration)
	}

	var content map[string]any
	var reservation *publisherpolicy.Reservation
	if profile.Profile == "dom.rendered-detail/v1" || profile.Profile == "jsonld.rendered-detail/v1" || profile.Profile == "embedded.rendered-detail/v1" {
		content, reservation, err = renderer.Fetch(ctx, profile)
		observed := observation.Snapshot()
		hostReachable = observed.Responses > 0
		hostFailure = observed.LastStatus == 401 || observed.LastStatus == 403 || observed.LastStatus == 429 || observed.LastStatus >= 500
	} else if profile.Profile == "jsonld.direct-detail/v1" || profile.Profile == "jsonld.proxy-detail/v1" || profile.Profile == "dom.direct-detail/v1" || profile.Profile == "dom.proxy-detail/v1" || profile.Profile == "embedded.direct-detail/v1" {
		fetched, failure := fetchDirectDetail(ctx, http, profile)
		err = failure
		hostReachable = fetched.Responses > 0
		hostFailure = fetched.Status == 401 || fetched.Status == 403 || fetched.Status == 429 || fetched.Status >= 500
		content = fetched.Content
		if fetched.ErrorKind == "tdm" {
			resource := fetched.FinalURL
			if resource == "" {
				resource = profile.SourceURL
			}
			reservation = &publisherpolicy.Reservation{URL: resource, Source: fetched.TDMSource}
			if len(fetched.TDMPolicy) > 8192 || !utf8.ValidString(fetched.TDMPolicy) || strings.ContainsRune(fetched.TDMPolicy, 0) {
				result.Diagnostics = append(result.Diagnostics, "invalid_policy_url")
			} else if fetched.TDMPolicy != "" {
				reservation.PolicyURL = &fetched.TDMPolicy
			}
		} else if err != nil && fetched.ErrorKind == "status" {
			err = &executor.NavigationHTTPError{RequestedURL: profile.SourceURL, ResponseURL: fetched.FinalURL, Status: uint32(fetched.Status)}
		}
	} else if profile.Profile == "adp.public-detail/v1" || (profile.Profile == "paylocity.html-detail/v1" || profile.Profile == "paylocity.proxy-html-detail/v1") || profile.Profile == "paycom.public-detail/v1" || profile.Profile == "rippling.v1-detail/v1" || profile.Profile == "mokahr.encrypted-detail/v1" || (profile.Profile == "eightfold.jsonld-api-detail/v1" || profile.Profile == "eightfold.proxy-jsonld-api-detail/v1") || profile.Profile == "smartrecruiters.api-detail/v1" || (profile.Profile == "workable.api-detail/v1" || profile.Profile == "workable.proxy-api-detail/v1") || profile.Profile == "join.nextdata-detail/v1" || profile.Profile == "oracle_hcm.api-detail/v1" || (profile.Profile == "api_sniffer.http-detail/v1" || profile.Profile == "api_sniffer.proxy-http-detail/v1") {
		content, reservation, err = fetchAPIDetail(ctx, http, profile)
		if reservation != nil && reservation.PolicyURL != nil && (len(*reservation.PolicyURL) > 8192 || !utf8.ValidString(*reservation.PolicyURL) || strings.ContainsRune(*reservation.PolicyURL, 0)) {
			reservation.PolicyURL = nil
			result.Diagnostics = append(result.Diagnostics, "invalid_policy_url")
		}
	} else if profile.Profile == "notion.public-detail/v1" || profile.Profile == "pdf.public-detail/v1" || profile.Profile == "jobstreet.graphql-detail/v1" || profile.Profile == "seek.graphql-detail/v1" || profile.Profile == "linkedin.guest-detail/v1" || profile.Profile == "jazzhr.public-detail/v1" || profile.Profile == "taleo.enterprise-detail/v1" || profile.Profile == "jobconvo.public-detail/v1" {
		content, reservation, err = fetchAPIDetail(ctx, http, profile)
	} else {
		fetched, failure := FetchWorkdayDetail(ctx, http, profile.SourceURL, profile.FacilityTenantAliases)
		err = failure
		hostReachable = fetched.Responses > 0
		hostFailure = fetched.TransportErrors > 0 || fetched.Status == 401 || fetched.Status == 403 || fetched.Status == 429 || fetched.Status >= 500
		if err == nil && !fetched.Gone {
			body, failure := json.Marshal(fetched.Content)
			if failure == nil {
				failure = json.Unmarshal(body, &content)
			}
			err = failure
		}
		if fetched.Gone {
			err = executor.ErrEmptyResult
		}
		var reserved *workday.ReservationError
		if errors.As(err, &reserved) {
			reservation = &publisherpolicy.Reservation{URL: reserved.URL, Source: "header"}
			if len(reserved.PolicyURL) > 8192 || !utf8.ValidString(reserved.PolicyURL) || strings.ContainsRune(reserved.PolicyURL, 0) {
				result.Diagnostics = append(result.Diagnostics, "invalid_policy_url")
			} else if reserved.PolicyURL != "" {
				reservation.PolicyURL = &reserved.PolicyURL
			}
		}
		var failed *workday.DetailFetchError
		if errors.As(err, &failed) {
			if failed.Kind == "transport" || failed.Kind == "invalid_payload" {
				hostFailure = true
			}
			if failed.Kind == "http" {
				err = &executor.NavigationHTTPError{RequestedURL: profile.Endpoint, ResponseURL: profile.Endpoint, Status: uint32(failed.Status)}
			}
		}
	}
	if reservation != nil && reservation.PolicyURL != nil && (len(*reservation.PolicyURL) > 8192 || !utf8.ValidString(*reservation.PolicyURL) || strings.ContainsRune(*reservation.PolicyURL, 0)) {
		reservation.PolicyURL = nil
		result.Diagnostics = append(result.Diagnostics, "invalid_policy_url")
	}
	result.DiscoveryDuration = time.Since(started)
	snapshot := observation.Snapshot()
	hostReachable = hostReachable || snapshot.Responses > 0
	hostFailure = hostFailure || snapshot.NoResponse > 0 || snapshot.LastStatus == 401 || snapshot.LastStatus == 403 || snapshot.LastStatus == 429 || snapshot.LastStatus >= 500
	if reservation != nil {
		result.DiscoveryError = true
		return finish(nil, reservation)
	}
	if err != nil {
		result.DiscoveryError = true
		result.DiscoveryCancelled = ctx.Err() != nil
		return finish(err, nil)
	}
	result.Discovered = 1
	receipt, err := PersistDetailContent(ctx, authority, detail, processor, content)
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

func fetchDirectDetail(ctx context.Context, verified *VerifiedDirectHTTP, profile queue.WorkdayDetailProfile) (jsonld.FetchResult, error) {
	if verified == nil || verified.client == nil || verified.proxyRequired != queue.ProfileRequiresProxy(profile.Profile) {
		return jsonld.FetchResult{}, queue.ErrConfiguration
	}
	client := *verified.client
	jar, err := cookiejar.New(nil)
	if err != nil {
		return jsonld.FetchResult{}, err
	}
	client.Jar = jar
	client.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	if profile.Profile == "embedded.direct-detail/v1" {
		return fetchEmbeddedDetail(ctx, &client, profile)
	}
	if profile.Profile == "dom.direct-detail/v1" || profile.Profile == "dom.proxy-detail/v1" {
		return dom.FetchDetailWithClient(ctx, profile.SourceURL, profile.DOMConfig, &client)
	}
	return jsonld.FetchDetailWithClient(ctx, jsonld.Request{URL: profile.SourceURL, Config: profile.JSONLDConfig}, &client)
}
