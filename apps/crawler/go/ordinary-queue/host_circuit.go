package queue

import (
	"context"
	_ "embed"
	"math"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/redis/go-redis/v9"
)

// These are the actual shared Python circuit scripts, not a new control plane.
//
//go:embed host_failure.lua
var hostFailureLua string

//go:embed host_success.lua
var hostSuccessLua string

type HostCircuitSettings struct {
	FailureThreshold                           int
	FailureWindow, OpenDuration, ProbeDuration time.Duration
}

func DefaultHostCircuitSettings() HostCircuitSettings {
	return HostCircuitSettings{3, 600 * time.Second, 1800 * time.Second, 600 * time.Second}
}

// HostCircuits owns no extra Redis pool. Settings are frozen startup inputs.
// All exported transitions below require installed ordinary attempt authority.
type HostCircuits struct {
	client           *Client
	settings         HostCircuitSettings
	failure, success *redis.Script
}

func NewHostCircuits(client *Client, settings HostCircuitSettings) (*HostCircuits, error) {
	if client == nil || settings.FailureThreshold < 1 || settings.FailureThreshold > math.MaxInt32 {
		return nil, ErrConfiguration
	}
	for _, duration := range []time.Duration{settings.FailureWindow, settings.OpenDuration, settings.ProbeDuration} {
		if duration <= 0 || duration%time.Second != 0 {
			return nil, ErrConfiguration
		}
	}
	return &HostCircuits{client: client, settings: settings, failure: redis.NewScript(hostFailureLua), success: redis.NewScript(hostSuccessLua)}, nil
}

func normalizeHost(host string) string {
	host = strings.TrimFunc(host, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	return strings.ToLower(strings.TrimRight(host, "."))
}

func validHost(host string) bool {
	return len(host) > 0 && len(host) <= 253 && utf8.ValidString(host) && !strings.ContainsRune(host, '|') && !strings.ContainsFunc(host, unicode.IsControl)
}

func circuitTime(value float64) (*time.Time, error) {
	if !validTime(value) || value > 253402300799 {
		return nil, ErrProtocol
	}
	if value == 0 {
		return nil, nil
	}
	whole, fraction := math.Modf(value)
	at := time.Unix(int64(whole), int64(fraction*1e9)).UTC()
	// PostgreSQL timestamps have microsecond resolution. Round upward so a
	// stronger Redis circuit bound cannot become an earlier canonical due time.
	if remainder := at.Nanosecond() % 1000; remainder != 0 {
		at = at.Add(time.Duration(1000-remainder) * time.Nanosecond)
	}
	return &at, nil
}

func (h *HostCircuits) openUntil(ctx context.Context, host string) (*float64, error) {
	raw, err := h.client.redis.Get(ctx, "host_open:"+host).Result()
	if err == redis.Nil {
		return nil, nil
	}
	if err != nil {
		return nil, ErrObservation
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || !validTime(value) || value > 253402300799 {
		// Match corrupt-marker fail-open behavior, also rejecting non-finite
		// timestamps that cannot enter a canonical database receipt.
		if err := h.client.redis.Del(ctx, "host_open:"+host).Err(); err != nil {
			return nil, ErrObservation
		}
		return nil, nil
	}
	return &value, nil
}

type HostCircuitOutcome struct {
	Host       string
	Failures   int64
	OpenUntil  *time.Time
	OpenedNow  bool
	Diagnostic error
}

func detachedHostOutcome(outcome HostCircuitOutcome) *HostCircuitOutcome {
	if outcome.OpenUntil != nil {
		at := *outcome.OpenUntil
		outcome.OpenUntil = &at
	}
	return &outcome
}

func (h *HostCircuits) recordFailure(ctx context.Context, host string) HostCircuitOutcome {
	outcome := HostCircuitOutcome{Host: host}
	now, err := h.client.clock(ctx)
	if err != nil {
		outcome.Diagnostic = ErrObservation
		return outcome
	}
	raw, err := h.failure.Run(ctx, h.client.redis, []string{"host_fail:" + host, "host_open:" + host, "host_probe:" + host}, h.settings.FailureThreshold, int64(h.settings.FailureWindow/time.Second), number(now), int64(h.settings.OpenDuration/time.Second), int64(h.settings.ProbeDuration/time.Second)).Result()
	if err != nil {
		outcome.Diagnostic = ErrObservation
		return outcome
	}
	values, ok := raw.([]any)
	if !ok || len(values) != 3 {
		outcome.Diagnostic = ErrProtocol
		return outcome
	}
	count, ok := values[0].(int64)
	opened, openedOK := values[2].(int64)
	text, textOK := values[1].(string)
	at, parseErr := strconv.ParseFloat(text, 64)
	if !ok || !openedOK || !textOK || count < 1 || opened < 0 || opened > 1 || parseErr != nil {
		outcome.Diagnostic = ErrProtocol
		return outcome
	}
	outcome.OpenUntil, err = circuitTime(at)
	if err != nil {
		outcome.Diagnostic = err
		return outcome
	}
	outcome.Failures, outcome.OpenedNow = count, opened == 1
	return outcome
}

func (h *HostCircuits) recordSuccess(ctx context.Context, host string) error {
	now, err := h.client.clock(ctx)
	if err != nil {
		return err
	}
	raw, err := h.success.Run(ctx, h.client.redis, []string{"host_fail:" + host, "host_open:" + host, "host_probe:" + host}, number(now)).Text()
	if err != nil {
		return ErrObservation
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil {
		return ErrProtocol
	}
	_, err = circuitTime(value)
	return err
}

// GreenhouseHostRun is private, claim-bound protective circuit state. It is not
// fetch evidence or independent permission to write. Ambiguous Redis outcomes
// are cached once per actual run, never blindly retried on SQL rollback.
type GreenhouseHostRun struct {
	mu                       sync.Mutex
	authority                *Authority
	claim                    *Claim
	circuits                 *HostCircuits
	host                     string
	preflightDone            bool
	status                   string
	deferUntil               *float64
	diagnostic               error
	failureDone, successDone bool
	failure                  HostCircuitOutcome
}

type GreenhouseHostPreflight struct {
	Run          *GreenhouseHostRun
	Receipt      *Receipt
	Host, Status string
	Diagnostic   error
}

// PreflightGreenhouseHost uses learned failure routing, then the configured
// board hostname, matching Python's strict Greenhouse preflight. It cannot run
// after discovery starts. Deferrals commit a future canonical PG deadline and
// receipt without changing success/failure, listing or provider-gone state.
func (a *Authority) PreflightGreenhouseHost(ctx context.Context, claim *Claim, circuits *HostCircuits) (*GreenhouseHostPreflight, error) {
	if a == nil || !a.valid(claim) || a.ownership == nil || claim.task.Kind != Monitor || claim.recovered != nil || circuits == nil || circuits.client != a.queue {
		return nil, ErrConfiguration
	}
	profile, err := InspectRichMonitor(claim.task.ID, claim.task.Config)
	if err != nil {
		return nil, err
	}
	claim.cycleMu.Lock()
	defer claim.cycleMu.Unlock()
	if claim.cycleStarted {
		return nil, ErrConfiguration
	}
	run := claim.hostRun
	if run == nil {
		host := normalizeHost(claim.task.Config["egress_host"])
		if host == "" {
			boardURL, _ := url.Parse(claim.task.Config["board_url"])
			host = normalizeHost(boardURL.Hostname())
		}
		if profile.BoardID != claim.task.ID || !validHost(host) {
			return nil, ErrConfiguration
		}
		run = &GreenhouseHostRun{authority: a, claim: claim, circuits: circuits, host: host}
		claim.hostRun = run
	} else if run.circuits != circuits {
		return nil, ErrConfiguration
	}
	run.mu.Lock()
	defer run.mu.Unlock()
	_, err = a.Write(ctx, claim, false, func(ctx context.Context, _ pgx.Tx) error {
		if run.preflightDone {
			return nil
		}
		run.preflightDone, run.status = true, "ready"
		open, err := circuits.openUntil(ctx, run.host)
		if err != nil {
			run.status, run.diagnostic = "unavailable", err
			return nil
		}
		if open == nil {
			return nil
		}
		now, err := a.queue.clock(ctx)
		if err != nil {
			run.status, run.diagnostic = "unavailable", err
			return nil
		}
		if *open > now {
			run.status, run.deferUntil = "host_circuit_open", open
			return nil
		}
		probe, err := a.queue.redis.SetNX(ctx, "host_probe:"+run.host, "1", circuits.settings.ProbeDuration).Result()
		if err != nil {
			run.status, run.diagnostic = "unavailable", ErrObservation
			return nil
		}
		if probe {
			run.status = "probe"
			return nil
		}
		due := now + circuits.settings.ProbeDuration.Seconds()
		run.status, run.deferUntil = "host_circuit_half_open", &due
		return nil
	})
	if err != nil {
		return nil, err
	}
	result := &GreenhouseHostPreflight{Run: run, Host: run.host, Status: run.status, Diagnostic: run.diagnostic}
	if run.deferUntil != nil {
		deadline, err := circuitTime(*run.deferUntil)
		if err != nil || deadline == nil {
			return nil, ErrProtocol
		}
		result.Receipt, err = a.Write(ctx, claim, true, func(ctx context.Context, tx pgx.Tx) error {
			_, err := tx.Exec(ctx, `UPDATE public.job_board SET next_check_at=GREATEST(next_check_at,$2::timestamptz,clock_timestamp()+interval '1 second'),lease_owner=NULL,leased_until=NULL,updated_at=now() WHERE id=$1::uuid`, claim.task.ID, *deadline)
			return err
		})
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}

// GreenhouseHostObservation is detached request accounting from the native
// transport. It never authorizes a database/queue effect without the run claim.
type GreenhouseHostObservation struct {
	Hosts    []string
	LastHost string
}

func (r *GreenhouseHostRun) valid(c *GreenhouseCycle) bool {
	return r != nil && r.authority == c.authority && r.claim == c.claim && r.preflightDone && r.deferUntil == nil
}

func (r *GreenhouseHostRun) failureOutcome(ctx context.Context, observation GreenhouseHostObservation) (*HostCircuitOutcome, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	host := normalizeHost(observation.LastHost)
	if host == "" {
		host = r.host
	}
	if !validHost(host) {
		return nil, ErrConfiguration
	}
	if r.successDone {
		return nil, ErrConfiguration
	}
	if !r.failureDone {
		r.failureDone = true // no mutation retry after an ambiguous reply
		r.failure = r.circuits.recordFailure(ctx, host)
	} else if r.failure.Host != host {
		return nil, ErrConfiguration
	}
	return detachedHostOutcome(r.failure), nil
}

// RecordGreenhouseHostSuccess runs after the canonical success receipt commits
// and before settlement. A protective Redis error does not revoke that receipt.
// Duplicate calls and ambiguous replies never replay completed host mutations.
func (a *Authority) RecordGreenhouseHostSuccess(ctx context.Context, run *GreenhouseHostRun, receipt *Receipt, observation GreenhouseHostObservation) error {
	if a == nil || run == nil || run.authority != a || receipt == nil || receipt.claim != run.claim || (receipt.terminalOutcome != "succeeded" && receipt.terminalOutcome != "publisher_reserved") || run.claim.recovered != nil || run.deferUntil != nil || len(observation.Hosts) > 64 {
		return ErrConfiguration
	}
	hosts := make(map[string]bool)
	for _, observed := range observation.Hosts {
		host := normalizeHost(observed)
		if !validHost(host) {
			return ErrConfiguration
		}
		hosts[host] = true
	}
	if len(hosts) == 0 {
		hosts[run.host] = true
	}
	run.mu.Lock()
	defer run.mu.Unlock()
	if run.failureDone {
		return ErrConfiguration
	}
	if run.successDone {
		return run.diagnostic
	}
	err := a.transaction(ctx, false, func(ctx context.Context, tx pgx.Tx) error {
		if err := a.requireOwnership(ctx, tx, run.claim); err != nil {
			return err
		}
		if err := a.current(ctx, run.claim); err != nil {
			return err
		}
		due, err := a.require(ctx, tx, run.claim, "completed")
		if err != nil {
			return err
		}
		canonical, err := canonicalDue(ctx, tx, run.claim)
		if err != nil {
			return err
		}
		if due == nil || receipt.nextDue == nil || canonical == nil || !due.Equal(*receipt.nextDue) || !canonical.Equal(*due) {
			return ErrAuthorityLost
		}
		run.successDone = true
		for host := range hosts {
			if err := run.circuits.recordSuccess(ctx, host); err != nil {
				run.diagnostic = err
				break
			}
		}
		return nil
	})
	if err != nil {
		return err
	}
	return run.diagnostic
}
