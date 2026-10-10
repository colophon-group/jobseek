package worker

import (
	"context"
	"errors"
	"log"
	"net"
	"net/http"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

type runtimeServices struct {
	claim     func(context.Context) (*queue.Claim, error)
	heartbeat func(context.Context, *queue.Claim) error
	execute   func(context.Context, *queue.Claim) (*GreenhouseClaimResult, error)
}

// A heartbeat must not race this runner's successful acknowledgement and
// cancel it because that acknowledgement correctly removed its own lease.
// This local guard never replaces the cross-process SQL/token barriers.
type claimLeaseGuard struct {
	mu      sync.Mutex
	settled bool
}
type claimLeaseGuardKey struct{}

func settleClaim(ctx context.Context, fn func() error) error {
	guard, _ := ctx.Value(claimLeaseGuardKey{}).(*claimLeaseGuard)
	if guard == nil {
		return fn()
	}
	guard.mu.Lock()
	defer guard.mu.Unlock()
	err := fn()
	if err == nil {
		guard.settled = true
	}
	return err
}

func runLeasedClaim(parent context.Context, c RuntimeConfig, s runtimeServices, claim *queue.Claim, m *runtimeMetrics) (*GreenhouseClaimResult, error) {
	ctx, cancel := context.WithTimeout(parent, c.taskTimeout)
	defer cancel()
	guard := &claimLeaseGuard{}
	ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
	stopped := make(chan struct{})
	beatDone := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(c.heartbeat)
		defer ticker.Stop()
		for {
			select {
			case <-stopped:
				beatDone <- nil
				return
			case <-ctx.Done():
				beatDone <- nil
				return
			case <-ticker.C:
				guard.mu.Lock()
				if guard.settled {
					guard.mu.Unlock()
					beatDone <- nil
					return
				}
				err := s.heartbeat(ctx, claim)
				guard.mu.Unlock()
				if err != nil {
					m.heartbeat(false)
					cancel()
					beatDone <- err
					return
				}
				m.heartbeat(true)
			}
		}
	}()

	type execution struct {
		result *GreenhouseClaimResult
		err    error
	}
	executed := make(chan execution, 1)
	go func() { result, err := s.execute(ctx, claim); executed <- execution{result, err} }()
	var completed execution
	select {
	case completed = <-executed:
	case <-ctx.Done():
		grace := time.NewTimer(c.cancellationGrace)
		select {
		case completed = <-executed:
			grace.Stop()
		case <-grace.C:
			close(stopped)
			return nil, ErrDrainTimeout
		}
	}
	close(stopped)
	grace := time.NewTimer(c.cancellationGrace)
	defer grace.Stop()
	var heartbeatErr error
	select {
	case heartbeatErr = <-beatDone:
	case <-grace.C:
		cancel()
		return completed.result, ErrDrainTimeout
	}
	if heartbeatErr != nil {
		return completed.result, claimRunError("heartbeat", heartbeatErr)
	}
	if ctx.Err() != nil && (completed.result == nil || !completed.result.Settled) {
		return completed.result, claimRunError("task", ctx.Err())
	}
	return completed.result, completed.err
}

// runWorkerLoop holds at most concurrency opaque claims. Signal cancellation
// first stops pops while task contexts and heartbeats remain live during drain.
// On expiry, unfinished work retains leases/receipts for the guarded reaper.
func runWorkerLoop(stop context.Context, c RuntimeConfig, s runtimeServices, m *runtimeMetrics) error {
	if c.concurrency < 1 || c.heartbeat <= 0 || c.taskTimeout <= 0 || c.idleBackoff <= 0 || c.stallTimeout <= 0 || s.claim == nil || s.heartbeat == nil || s.execute == nil || m == nil {
		return ErrStartup
	}
	claims, stopClaims := context.WithCancel(stop)
	defer stopClaims()
	tasks, cancelTasks := context.WithCancel(context.WithoutCancel(stop))
	defer cancelTasks()
	var wg sync.WaitGroup
	var unsafeTask atomic.Bool
	failures := make(chan error, c.concurrency)
	for id := 0; id < c.concurrency; id++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			for {
				if claims.Err() != nil {
					return
				}
				claim, err := s.claim(claims)
				if err != nil {
					if claims.Err() != nil {
						return
					}
					m.claimError()
					log.Print(claimRunError("claim", err))
					if errors.Is(err, queue.ErrAuthorityLost) || errors.Is(err, queue.ErrConfiguration) || errors.Is(err, queue.ErrUnsupportedProfile) {
						failures <- claimRunError("claim", err)
						return
					}
					if !waitRuntime(claims, c.idleBackoff) {
						return
					}
					continue
				}
				m.progress(id)
				if claim == nil {
					if !waitRuntime(claims, c.idleBackoff) {
						return
					}
					continue
				}
				// A pop racing shutdown is retained and drained, never silently dropped.
				m.active.Add(1)
				started := time.Now()
				result, err := runLeasedClaim(tasks, c, s, claim, m)
				if errors.Is(err, ErrDrainTimeout) {
					unsafeTask.Store(true)
				}
				m.record(result, err, time.Since(started))
				if err != nil {
					logRuntimeTaskError(err)
				}
				m.active.Add(-1)
				m.progress(id)
				if err != nil && (errors.Is(err, queue.ErrAuthorityLost) || errors.Is(err, queue.ErrConfiguration) || errors.Is(err, queue.ErrUnsupportedProfile) || errors.Is(err, ErrDrainTimeout)) {
					failures <- err
					return
				}
			}
		}(id)
	}
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	watchdog := time.NewTicker(min(time.Second, c.stallTimeout/2))
	defer watchdog.Stop()
	var fatal error
wait:
	for {
		select {
		case <-stop.Done():
			break wait
		case fatal = <-failures:
			break wait
		case <-done:
			if stop.Err() == nil {
				fatal = errors.New("ordinary worker loop exited")
			}
			break wait
		case <-watchdog.C:
			if m.stalled(c.stallTimeout) {
				fatal = errors.New("ordinary worker loop stalled")
				break wait
			}
		}
	}
	m.ready.Store(false)
	stopClaims()
	if fatal != nil {
		cancelTasks()
	} else {
		timer := time.NewTimer(c.shutdownGrace)
		select {
		case <-done:
			timer.Stop()
			m.drain(false)
			return nil
		case <-timer.C:
		}
		m.drain(true)
		m.cancelled.Add(uint64(m.active.Load()))
		cancelTasks()
	}
	timer := time.NewTimer(c.cancellationGrace)
	defer timer.Stop()
	select {
	case <-done:
		if unsafeTask.Load() {
			return ErrDrainTimeout
		}
		return fatal
	case <-timer.C:
		return ErrDrainTimeout
	}
}

// Nonfatal failures retain their attempts for recovery. Emit only the existing
// bounded phase/class diagnostic; never log an upstream or database message.
func logRuntimeTaskError(err error) {
	var diagnostic *ClaimRunError
	if !errors.As(err, &diagnostic) {
		diagnostic = claimRunError("task", err).(*ClaimRunError)
	}
	log.Print(diagnostic.Error())
}
func waitRuntime(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

// runtimeStartupFailure reports only a fixed code-owned stage. Raw errors can
// contain database URLs, credentials or board data and must never be logged.
func runtimeStartupFailure(stage string) error {
	log.Printf("ordinary worker startup stage: %s", stage)
	return ErrStartup
}

// Run loads native process assets and exact owned authority before opening its
// health/metrics listener. No activation or production scheduling occurs here.
// Callers must exit on error; uncooperative cancellation skips blocking cleanup.
func Run(ctx context.Context, c RuntimeConfig) error {
	if c.source == "" || c.plan == "" || c.epoch < 1 || c.databaseURL == "" || c.redisURL == "" || c.concurrency < 1 {
		return runtimeStartupFailure("configuration")
	}
	startup, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	client, err := queue.Open(c.redisURL, queue.Settings{DefaultDelaySeconds: c.delay, LeaseTTL: c.leaseTTL, MaxDomains: c.maxDomains})
	if err != nil {
		return runtimeStartupFailure("redis_client")
	}
	var authority *queue.Authority
	var lookup *executor.Store
	var locations *executor.Locations
	var httpClient *VerifiedDirectHTTP
	var workdayHTTP *VerifiedDirectHTTP
	var skipSSLHTTP, skipSSLHTTP2 *VerifiedDirectHTTP
	var proxyHTTP *VerifiedHTTP
	cleanup := func() {
		if skipSSLHTTP != nil {
			skipSSLHTTP.CloseIdleConnections()
		}
		if skipSSLHTTP2 != nil {
			skipSSLHTTP2.CloseIdleConnections()
		}
		if proxyHTTP != nil {
			proxyHTTP.CloseIdleConnections()
		}
		if workdayHTTP != nil {
			workdayHTTP.CloseIdleConnections()
		}
		if httpClient != nil {
			httpClient.CloseIdleConnections()
		}
		if locations != nil {
			_ = locations.Close()
		}
		if lookup != nil {
			lookup.Close()
		}
		if authority != nil {
			authority.Close()
		}
		_ = client.Close()
	}
	authority, err = queue.OpenOwnedAuthority(startup, c.databaseURL, client, c.epoch, c.plan, c.source)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("ownership")
	}
	if authority.RequiresRenderedDetails() && !c.rendered {
		cleanup()
		return runtimeStartupFailure("rendered_mode")
	}
	// The separately installed SHA1 is what the legacy owner also receives.
	if err = authority.AttestOwnershipProjection(startup, c.projection); err != nil {
		cleanup()
		return runtimeStartupFailure("projection")
	}
	lookup, err = executor.OpenOrdinaryLookupStore(startup, c.databaseURL)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("lookup_store")
	}
	matcher, err := enrichment.Load(c.dataDirectory)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("enrichment_assets")
	}
	lookups, err := executor.LoadLookups(startup, lookup)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("lookups")
	}
	locations, err = executor.LoadLocations(startup, lookup)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("locations")
	}
	httpClient, err = NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: c.internalHosts})
	if err != nil {
		cleanup()
		return runtimeStartupFailure("direct_transport")
	}
	workdayHTTP, err = NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: c.internalHosts, EnableHTTP2: true})
	if err != nil {
		cleanup()
		return runtimeStartupFailure("http2_transport")
	}
	skipSSLHTTP, err = NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: c.internalHosts, SkipSSL: true})
	if err != nil {
		cleanup()
		return runtimeStartupFailure("explicit_http_tls_transport")
	}
	skipSSLHTTP2, err = NewVerifiedDirectHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: c.internalHosts, EnableHTTP2: true, SkipSSL: true})
	if err != nil {
		cleanup()
		return runtimeStartupFailure("explicit_http2_tls_transport")
	}

	if authority.RequiresProxyHTTP() {
		proxyHTTP, err = newVerifiedProxyHTTP(DirectHTTPConfig{CABundlePEM: pinnedCA, InternalHosts: c.internalHosts}, c.proxy)
		if err != nil {
			cleanup()
			return runtimeStartupFailure("proxy_transport")
		}
	}
	circuits, err := queue.NewHostCircuits(client, c.circuits)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("host_circuits")
	}
	preparer := NativeRichPreparer{&executor.Processor{Matcher: matcher, Lookups: lookups, Locations: locations}}
	var renderer renderedDetailClient
	if c.rendered {
		renderer, err = installedRenderedDetails()
		if err != nil {
			cleanup()
			return runtimeStartupFailure("renderer_assets")
		}
	}
	if startup.Err() != nil {
		cleanup()
		return runtimeStartupFailure("startup_deadline")
	}
	listener, err := net.Listen("tcp", c.metricsAddress)
	if err != nil {
		cleanup()
		return runtimeStartupFailure("metrics_listener")
	}
	m := newRuntimeMetrics(c.concurrency, c.stallTimeout)
	m.source, m.plan, m.epoch = c.source, c.plan, c.epoch
	m.ready.Store(true)
	server := &http.Server{Handler: m, ReadHeaderTimeout: time.Second, ReadTimeout: 3 * time.Second, WriteTimeout: 3 * time.Second, IdleTimeout: 5 * time.Second}
	serving := make(chan error, 1)
	go func() { serving <- server.Serve(listener) }()
	process, stop := context.WithCancel(ctx)
	defer stop()
	serverFailure := make(chan error, 1)
	go func() {
		select {
		case err := <-serving:
			if !errors.Is(err, http.ErrServerClosed) {
				serverFailure <- errors.New("ordinary metrics server failed")
				stop()
			}
		case <-process.Done():
		}
	}()
	var claimTurn atomic.Uint64
	services := runtimeServices{claim: func(ctx context.Context) (*queue.Claim, error) {
		worker := queue.Simple
		if renderer != nil && claimTurn.Add(1)%2 == 0 {
			worker = queue.Browser
		}
		claim, err := authority.Claim(ctx, worker)
		if err != nil || claim != nil || renderer == nil {
			return claim, err
		}
		if worker == queue.Simple {
			worker = queue.Browser
		} else {
			worker = queue.Simple
		}
		return authority.Claim(ctx, worker)
	}, heartbeat: authority.Heartbeat, execute: func(ctx context.Context, claim *queue.Claim) (*GreenhouseClaimResult, error) {
		useProxy, err := runtimeClaimUsesProxy(ctx, authority, claim)
		if err != nil {
			return nil, claimRunError("transport_selection", err)
		}
		skip, err := runtimeClaimSkipsSSL(ctx, authority, claim)
		if err != nil {
			return nil, claimRunError("transport_selection", err)
		}
		if skip && claim.Descriptor().Kind == queue.Scrape {
			return RunDetail(ctx, authority, claim, skipSSLHTTP2, preparer.Processor, circuits, renderer)
		}
		if claim.Descriptor().Kind == queue.Monitor {
			if skip {
				transport := skipSSLHTTP
				if provider := claim.Descriptor().Config["crawler_type"]; provider == "sitemap" || provider == "workday" {
					transport = skipSSLHTTP2
				}
				return RunGreenhouseClaim(ctx, authority, claim, transport, preparer, circuits)
			}
		}

		if useProxy {
			if claim.Descriptor().Kind == queue.Scrape {
				return RunDetail(ctx, authority, claim, proxyHTTP, preparer.Processor, circuits, renderer)
			}
			return RunGreenhouseClaim(ctx, authority, claim, proxyHTTP, preparer, circuits)
		}
		if claim.Descriptor().Kind == queue.Scrape {
			return RunDetail(ctx, authority, claim, workdayHTTP, preparer.Processor, circuits, renderer)
		}
		if provider := claim.Descriptor().Config["crawler_type"]; provider == "workday" || provider == "smartrecruiters" || provider == "workable" || provider == "join" || provider == "sitemap" {
			return RunGreenhouseClaim(ctx, authority, claim, workdayHTTP, preparer, circuits)
		}
		monitorRenderer, _ := renderer.(renderedMonitorClient)
		return RunGreenhouseClaim(ctx, authority, claim, httpClient, preparer, circuits, monitorRenderer)
	}}
	err = runWorkerLoop(process, c, services, m)
	_ = server.Close()
	if !errors.Is(err, ErrDrainTimeout) {
		cleanup()
	}
	select {
	case failure := <-serverFailure:
		if err == nil {
			err = failure
		}
	default:
	}
	return err
}

// CheckHealth interrogates the installed process without creating another DB
// pool or loading taxonomy/location assets. Identity and live claim-loop progress
// must match this binary's protected startup binding.
func CheckHealth(ctx context.Context, c RuntimeConfig) error {
	host, port, err := net.SplitHostPort(c.metricsAddress)
	if err != nil || c.source == "" || c.plan == "" || c.epoch < 1 {
		return ErrStartup
	}
	if host == "0.0.0.0" {
		host = "127.0.0.1"
	}
	if host == "::" {
		host = "::1"
	}
	probe, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	transport := &http.Transport{Proxy: nil}
	defer transport.CloseIdleConnections()
	request, err := http.NewRequestWithContext(probe, http.MethodGet, "http://"+net.JoinHostPort(host, port)+"/healthz", nil)
	if err != nil {
		return ErrStartup
	}
	response, err := (&http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}).Do(request)
	if err != nil {
		return errors.New("ordinary worker health unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusNoContent || response.Header.Get("Jobseek-Ordinary-Source") != c.source || response.Header.Get("Jobseek-Ordinary-Plan") != c.plan || response.Header.Get("Jobseek-Ordinary-Epoch") != strconv.FormatInt(c.epoch, 10) {
		return errors.New("ordinary worker health identity rejected")
	}
	return nil
}
