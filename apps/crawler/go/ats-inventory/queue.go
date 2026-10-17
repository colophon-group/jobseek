package main

import (
	"context"
	"errors"
	"fmt"
	"math"
	"math/rand/v2"
	"regexp"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"
)

type inventoryQueuePolicy struct {
	LowWater         int     `json:"low_water"`
	Target           int     `json:"target"`
	HardCap          int     `json:"hard_cap"`
	PerTickCap       int     `json:"per_tick_cap"`
	DailyCap         int     `json:"daily_cap"`
	RolloutCap       int     `json:"rollout_cap"`
	ClaimTTLSeconds  int     `json:"claim_ttl_seconds"`
	JitterMinSeconds float64 `json:"jitter_min_seconds"`
	JitterMaxSeconds float64 `json:"jitter_max_seconds"`
}

func defaultInventoryQueuePolicy() inventoryQueuePolicy {
	return inventoryQueuePolicy{450, 500, 600, 25, 50, 1, 4 * 60 * 60, 1, 3}
}
func (p inventoryQueuePolicy) validate() error {
	if p.LowWater < 0 || p.LowWater > p.Target || p.Target > p.HardCap || p.PerTickCap < 1 || p.DailyCap < 1 || !slices.Contains([]int{1, 5, 25}, p.RolloutCap) || p.ClaimTTLSeconds < 1 || !(0 <= p.JitterMinSeconds && p.JitterMinSeconds <= p.JitterMaxSeconds) {
		return fmt.Errorf("invalid inventory queue policy")
	}
	return nil
}

type inventoryQueueSnapshot struct {
	TotalOpen                     int   `json:"total_open"`
	Available                     int   `json:"available"`
	FreshClaimed                  int   `json:"fresh_claimed"`
	ActiveLinkedPR                int   `json:"active_linked_pr"`
	ImportOpen                    int   `json:"import_open"`
	HumanOpen                     int   `json:"human_open"`
	ImportClosed                  int   `json:"import_closed"`
	ImportFreshClaimed            int   `json:"import_fresh_claimed"`
	ImportActiveLinkedPR          int   `json:"import_active_linked_pr"`
	ImportPickupLatencySamples    int   `json:"import_pickup_latency_samples"`
	ImportPickupLatencyAvgSeconds *int  `json:"import_pickup_latency_avg_seconds"`
	ImportPickupLatencyMaxSeconds *int  `json:"import_pickup_latency_max_seconds"`
	UnparseableClaims             int   `json:"unparseable_claims"`
	FreshClaimIssueNumbers        []int `json:"fresh_claim_issue_numbers"`
	LinkedPRIssueNumbers          []int `json:"linked_pr_issue_numbers"`
}

var inventoryClosesPattern = regexp.MustCompile(`(?i)(?:^|[^\pL\pN_])(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#([0-9]+)\b`)

func sortedInventoryInts(set map[int]bool) []int {
	out := make([]int, 0, len(set))
	for n := range set {
		out = append(out, n)
	}
	sort.Ints(out)
	return out
}
func classifyInventoryQueue(items []githubWorkItem, claims []githubClaim, now time.Time, ttl int) inventoryQueueSnapshot {
	open, imports := map[int]githubWorkItem{}, map[int]githubWorkItem{}
	for _, item := range items {
		if item.Kind == "issue" && item.State == "open" {
			open[item.Number] = item
		}
		if item.Kind == "issue" && slices.Contains(item.Labels, atsInventoryLabel) {
			imports[item.Number] = item
		}
	}
	linked := map[int]bool{}
	for _, item := range items {
		if item.Kind != "pr" || item.State != "open" {
			continue
		}
		for _, m := range inventoryClosesPattern.FindAllStringSubmatch(item.Body, -1) {
			n, e := strconv.Atoi(m[1])
			if e == nil {
				if _, ok := open[n]; ok {
					linked[n] = true
				}
			}
		}
	}
	cutoff := now.Add(-time.Duration(ttl) * time.Second)
	fresh := map[int]bool{}
	latencies := map[int]int{}
	unparseable := 0
	for _, claim := range claims {
		if _, ok := open[claim.IssueNumber]; !ok || !strings.HasPrefix(claim.Body, "<!-- ws-claim -->") {
			continue
		}
		at, e := parseInventoryTime(claim.CreatedAt)
		if e != nil {
			unparseable++
			fresh[claim.IssueNumber] = true
			continue
		}
		if at.Before(cutoff) {
			continue
		}
		fresh[claim.IssueNumber] = true
		if item, ok := imports[claim.IssueNumber]; ok && item.CreatedAt != nil {
			if created, e := parseInventoryTime(*item.CreatedAt); e == nil && !at.Before(created) {
				n := int(at.Sub(created).Seconds())
				prior, has := latencies[claim.IssueNumber]
				if !has || n < prior {
					latencies[claim.IssueNumber] = n
				}
			}
		}
	}
	out := inventoryQueueSnapshot{TotalOpen: len(open), FreshClaimed: len(fresh), ActiveLinkedPR: len(linked), UnparseableClaims: unparseable, FreshClaimIssueNumbers: sortedInventoryInts(fresh), LinkedPRIssueNumbers: sortedInventoryInts(linked), ImportPickupLatencySamples: len(latencies)}
	for number, item := range open {
		if !fresh[number] && !linked[number] {
			out.Available++
		}
		if slices.Contains(item.Labels, atsInventoryLabel) {
			out.ImportOpen++
		}
	}
	out.HumanOpen = len(open) - out.ImportOpen
	for number, item := range imports {
		if item.State != "open" {
			out.ImportClosed++
		}
		if _, ok := open[number]; ok {
			if fresh[number] {
				out.ImportFreshClaimed++
			}
			if linked[number] {
				out.ImportActiveLinkedPR++
			}
		}
	}
	if len(latencies) > 0 {
		sum, maximum := 0, 0
		for _, v := range latencies {
			sum += v
			maximum = max(maximum, v)
		}
		avg := int(math.RoundToEven(float64(sum) / float64(len(latencies))))
		out.ImportPickupLatencyAvgSeconds = &avg
		out.ImportPickupLatencyMaxSeconds = &maximum
	}
	return out
}
func requestedInventoryCreates(queue inventoryQueueSnapshot, createdToday int, p inventoryQueuePolicy) (int, string) {
	if queue.Available >= p.LowWater {
		return 0, "healthy"
	}
	requested := max(0, min(p.Target-queue.Available, p.HardCap-queue.TotalOpen, p.PerTickCap, p.DailyCap-createdToday, p.RolloutCap))
	if requested > 0 {
		return requested, "refill_needed"
	}
	if queue.TotalOpen >= p.HardCap {
		return 0, "hard_cap"
	}
	if createdToday >= p.DailyCap {
		return 0, "daily_cap"
	}
	return 0, "no_capacity"
}

type inventoryQueueRefiller struct {
	Coordinator      *candidateIssueCoordinator
	Ledger           *candidateLedger
	Items            []githubWorkItem
	Claims           []githubClaim
	Policy           inventoryQueuePolicy
	Now              func() time.Time
	Sleep            func(context.Context, time.Duration) error
	Jitter           func(float64, float64) float64
	RefreshOpenCount func(context.Context) (int, error)
}

func (r *inventoryQueueRefiller) run(ctx context.Context, companies []companyImpact, mode, admissionBlock string) (map[string]any, error) {
	if e := r.Policy.validate(); e != nil {
		return nil, e
	}
	if mode != "report" && mode != "dry-run" && mode != "refill" {
		return nil, fmt.Errorf("invalid queue mode")
	}
	now := time.Now()
	if r.Now != nil {
		now = r.Now()
	}
	now = now.UTC()
	before := classifyInventoryQueue(r.Items, r.Claims, now, r.Policy.ClaimTTLSeconds)
	day := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC).Unix()
	createdToday, e := r.Ledger.countCreatedSince(ctx, day)
	if e != nil {
		return nil, e
	}
	requested, status := requestedInventoryCreates(before, createdToday, r.Policy)
	if admissionBlock != "" {
		requested = 0
		status = admissionBlock
	}
	inspected, selected, created := 0, 0, 0
	hardSkips := map[string]int{}
	actions := []candidateIssueAction{}
	latestOpen := before.TotalOpen
	var retryAfter, retryAt *int64
	report := func() map[string]any {
		client := r.Coordinator.Client
		return map[string]any{"mode": mode, "status": status, "queue_before": before, "available_after": before.Available + created, "total_open_after": latestOpen, "created_today_before": createdToday, "requested_creates": requested, "inspected_candidates": inspected, "selected_candidates": selected, "created": created, "hard_skip_counts": hardSkips, "actions": actions, "rate_remaining": client.RateRemaining, "rate_reset": client.RateReset, "retry_after": retryAfter, "retry_at": retryAt}
	}
	if admissionBlock != "" || mode == "report" || requested == 0 {
		return report(), nil
	}
	registry, e := loadCompatibility()
	if e != nil {
		return nil, e
	}
	sorted := append([]companyImpact{}, companies...)
	sort.SliceStable(sorted, func(i, j int) bool { return impactLess(sorted[i], sorted[j]) })
	reserved := map[string]bool{}
	rateFailure := func(e error) bool {
		var limited *githubRateLimitError
		if errors.As(e, &limited) {
			status = "rate_limited"
			retryAfter = limited.RetryAfter
			retryAt = limited.retryAt(now.Unix())
			return true
		}
		return false
	}
	for _, company := range sorted {
		if selected >= requested {
			break
		}
		if e = ctx.Err(); e != nil {
			return nil, e
		}
		inspected++
		candidate, e := candidateFromImpact(company, registry)
		if e != nil {
			hardSkips["unsupported_family"]++
			continue
		}
		if reserved[candidate.SourceKey] {
			hardSkips["selected_source_key"]++
			continue
		}
		plan := r.Coordinator.plan(candidate)
		if !plan.eligible() {
			for _, evidence := range plan.HardSkips {
				hardSkips[evidence.Code]++
			}
			continue
		}
		if mode == "refill" {
			if r.RefreshOpenCount == nil {
				return nil, fmt.Errorf("refill requires live open-queue counter")
			}
			refreshed, e := r.RefreshOpenCount(ctx)
			if e != nil {
				if rateFailure(e) {
					break
				}
				return nil, e
			}
			latestOpen = max(refreshed, before.TotalOpen+created)
			if latestOpen >= r.Policy.HardCap-1 {
				status = "hard_cap_live"
				break
			}
		}
		action, e := r.Coordinator.create(ctx, candidate, mode == "dry-run")
		if e != nil {
			if rateFailure(e) {
				break
			}
			return nil, e
		}
		selected++
		reserved[candidate.SourceKey] = true
		actions = append(actions, action)
		if action.Action == "created" || action.Action == "created_reconciled" {
			created++
			latestOpen++
			if selected < requested {
				sleep := r.Sleep
				if sleep == nil {
					sleep = sleepInventoryContext
				}
				jitter := r.Jitter
				if jitter == nil {
					jitter = func(a, b float64) float64 { return a + rand.Float64()*(b-a) }
				}
				if e = sleep(ctx, time.Duration(jitter(r.Policy.JitterMinSeconds, r.Policy.JitterMaxSeconds)*float64(time.Second))); e != nil {
					return nil, e
				}
			}
		}
	}
	if status != "rate_limited" && status != "hard_cap_live" {
		if selected < requested {
			status = "candidate_pool_exhausted"
		} else if mode == "dry-run" {
			status = "dry_run"
		} else {
			status = "refilled"
		}
	}
	return report(), nil
}
