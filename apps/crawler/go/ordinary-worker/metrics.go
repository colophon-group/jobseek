package worker

import (
	"context"
	"errors"
	"fmt"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type runtimeHistogram struct {
	bounds  []float64
	buckets []uint64
	count   uint64
	sum     float64
}

func histogram(bounds ...float64) *runtimeHistogram {
	return &runtimeHistogram{bounds: bounds, buckets: make([]uint64, len(bounds)+1)}
}
func (h *runtimeHistogram) observe(seconds float64) {
	h.count++
	h.sum += seconds
	index := len(h.bounds)
	for i, bound := range h.bounds {
		if seconds <= bound {
			index = i
			break
		}
	}
	h.buckets[index]++
}
func (h *runtimeHistogram) write(b *strings.Builder, name, labels string) {
	var cumulative uint64
	for i, bound := range h.bounds {
		cumulative += h.buckets[i]
		fmt.Fprintf(b, "%s_bucket{%s,le=%q} %d\n", name, labels, strconv.FormatFloat(bound, 'g', -1, 64), cumulative)
	}
	cumulative += h.buckets[len(h.bounds)]
	fmt.Fprintf(b, "%s_bucket{%s,le=\"+Inf\"} %d\n%s_count{%s} %d\n%s_sum{%s} %g\n", name, labels, cumulative, name, labels, h.count, name, labels, h.sum)
}

type runtimeMetrics struct {
	source, plan                                    string
	epoch                                           int64
	ready                                           atomic.Bool
	active                                          atomic.Int64
	cancelled                                       atomic.Uint64
	mu                                              sync.Mutex
	lastProgress                                    time.Time
	workers                                         []time.Time
	stallTimeout                                    time.Duration
	claimsFailed, extended, lost, drained, timedout uint64
	runtimeLaneMetrics
	detail runtimeLaneMetrics
}

type runtimeLaneMetrics struct {
	tasks                                             map[string]uint64
	requests, responses, noResponse, bytes            int64
	taskDuration, monitorDuration, extractionDuration *runtimeHistogram
	extraction                                        map[string]uint64
	output                                            int64
	inserted, touched, relisted, gone                 int64
}

func newRuntimeLaneMetrics() runtimeLaneMetrics {
	return runtimeLaneMetrics{tasks: map[string]uint64{}, extraction: map[string]uint64{}, taskDuration: histogram(1, 2, 5, 10, 15, 30, 60, 120, 300), monitorDuration: histogram(.5, 1, 2, 5, 10, 30, 60, 120, 300), extractionDuration: histogram(.01, .05, .1, .25, .5, 1, 2, 5, 10, 30, 60, 120, 300)}
}

func newRuntimeMetrics(workers int, stall time.Duration) *runtimeMetrics {
	m := &runtimeMetrics{lastProgress: time.Now(), workers: make([]time.Time, workers), stallTimeout: stall, runtimeLaneMetrics: newRuntimeLaneMetrics(), detail: newRuntimeLaneMetrics()}
	for i := range m.workers {
		m.workers[i] = m.lastProgress
	}
	return m
}
func (m *runtimeMetrics) progress(worker int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.lastProgress = time.Now()
	m.workers[worker] = m.lastProgress
}
func (m *runtimeMetrics) stalled(timeout time.Duration) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return time.Since(m.lastProgress) >= timeout
}
func (m *runtimeMetrics) heartbeat(ok bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if ok {
		m.extended++
	} else {
		m.lost++
	}
}
func (m *runtimeMetrics) claimError() { m.mu.Lock(); defer m.mu.Unlock(); m.claimsFailed++ }
func (m *runtimeMetrics) drain(timeout bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if timeout {
		m.timedout++
	} else {
		m.drained++
	}
}
func runtimeTaskStatus(result *GreenhouseClaimResult, err error) string {
	if err != nil {
		switch {
		case errors.Is(err, context.Canceled):
			return "cancelled"
		case errors.Is(err, context.DeadlineExceeded):
			return "timeout"
		case errors.Is(err, queue.ErrAuthorityLost):
			return "authority_lost"
		default:
			return "unacknowledged"
		}
	}
	if result == nil || !result.Settled || result.Cycle == nil {
		return "unacknowledged"
	}
	switch result.Cycle.Status {
	case "unscheduled":
		return "skipped"
	case "publisher_reserved":
		return "tdm_reserved"
	case "gone", "gone_pending":
		return "gone"
	case "succeeded", "failed", "host_circuit_open", "host_circuit_half_open", "recovered":
		return result.Cycle.Status
	default:
		return "unacknowledged"
	}
}
func (m *runtimeMetrics) record(result *GreenhouseClaimResult, err error, duration time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	lane := &m.runtimeLaneMetrics
	if result != nil && result.TaskKind == queue.Scrape {
		lane = &m.detail
	}
	status := runtimeTaskStatus(result, err)
	lane.tasks[status]++
	lane.taskDuration.observe(duration.Seconds())
	lane.monitorDuration.observe(duration.Seconds())
	if result == nil {
		return
	}
	lane.requests += result.HTTP.Requests
	lane.responses += result.HTTP.Responses
	lane.noResponse += result.HTTP.NoResponse
	lane.bytes += result.HTTP.EncodedBytes
	lane.inserted += int64(result.Batches.Inserted)
	lane.touched += int64(result.Batches.Touched)
	lane.relisted += int64(result.Batches.Relisted)
	if result.Cycle != nil {
		lane.gone += int64(result.Cycle.Gone)
	}
	if result.DiscoveryStarted {
		outcome := "success"
		if result.DiscoveryError {
			outcome = "error"
		}
		if result.DiscoveryCancelled {
			outcome = "cancelled"
		}
		lane.extraction[outcome]++
		lane.extractionDuration.observe(result.DiscoveryDuration.Seconds())
		lane.output += int64(result.Discovered)
	}
}
func (m *runtimeMetrics) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != "GET" {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	if r.URL.Path == "/healthz" {
		if !m.ready.Load() || m.stalled(m.stallTimeout) {
			http.Error(w, "not ready", http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Jobseek-Ordinary-Source", m.source)
		w.Header().Set("Jobseek-Ordinary-Plan", m.plan)
		w.Header().Set("Jobseek-Ordinary-Epoch", strconv.FormatInt(m.epoch, 10))
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.URL.Path != "/metrics" {
		http.NotFound(w, r)
		return
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	var b strings.Builder
	fmt.Fprintf(&b, "jobseek_ordinary_go_ready %d\njobseek_ordinary_go_active_claims %d\njobseek_ordinary_go_claim_errors_total %d\n", boolNumber(m.ready.Load()), m.active.Load(), m.claimsFailed)
	m.runtimeLaneMetrics.write(&b, "monitor")
	m.detail.write(&b, "scrape")
	for i, at := range m.workers {
		fmt.Fprintf(&b, "crawler_worker_heartbeat_timestamp_seconds{worker_id=%q} %g\n", strconv.Itoa(i), float64(at.UnixNano())/1e9)
	}
	fmt.Fprintf(&b, "crawler_inflight_heartbeat_total{wtype=\"simple\",outcome=\"extended\"} %d\ncrawler_inflight_heartbeat_total{wtype=\"simple\",outcome=\"lost\"} %d\n", m.extended, m.lost)
	fmt.Fprintf(&b, "crawler_shutdown_drain_total{wtype=\"simple\",outcome=\"drained\"} %d\ncrawler_shutdown_drain_total{wtype=\"simple\",outcome=\"timeout\"} %d\ncrawler_shutdown_cancelled_total{wtype=\"simple\"} %d\n", m.drained, m.timedout, m.cancelled.Load())

	for _, item := range []struct {
		action string
		value  int64
	}{{"new", m.inserted}, {"relisted", m.relisted}, {"gone", m.gone}} {
		fmt.Fprintf(&b, "crawler_monitor_jobs_discovered_total{profile=\"simple\",action=%q} %d\n", item.action, item.value)
	}
	fmt.Fprintf(&b, "jobseek_ordinary_go_posting_touches_total %d\n", m.touched)
	w.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = w.Write([]byte(b.String()))
}
func boolNumber(value bool) int {
	if value {
		return 1
	}
	return 0
}

func (lane *runtimeLaneMetrics) write(b *strings.Builder, kind string) {
	statuses := make([]string, 0, len(lane.tasks))
	for status := range lane.tasks {
		statuses = append(statuses, status)
	}
	sort.Strings(statuses)
	for _, status := range statuses {
		fmt.Fprintf(b, "crawler_tasks_total{kind=%q,status=%q} %d\n", kind, status, lane.tasks[status])
	}
	lane.taskDuration.write(b, "crawler_task_duration_seconds", `kind="`+kind+`"`)
	if kind == "monitor" {
		lane.monitorDuration.write(b, "crawler_monitor_duration_seconds", `profile="simple"`)
	} else {
		lane.monitorDuration.write(b, "crawler_scrape_duration_seconds", `profile="simple"`)
	}
	labels := `stage="` + kind + `",execution_class="http",egress="direct"`
	fmt.Fprintf(b, "crawler_runtime_origin_attempts_total{%s} %d\ncrawler_runtime_origin_outcomes_total{%s,outcome=\"response\"} %d\ncrawler_runtime_origin_outcomes_total{%s,outcome=\"transport_error\"} %d\ncrawler_runtime_response_body_bytes_total{%s} %d\n", labels, lane.requests, labels, lane.responses, labels, lane.noResponse, labels, lane.bytes)
	for _, outcome := range []string{"success", "error", "cancelled"} {
		fmt.Fprintf(b, "crawler_runtime_executions_total{stage=%q,implementation=\"go\",outcome=%q} %d\n", kind, outcome, lane.extraction[outcome])
	}
	lane.extractionDuration.write(b, "crawler_runtime_execution_duration_seconds", `stage="`+kind+`",implementation="go"`)
	fmt.Fprintf(b, "crawler_runtime_output_items_total{stage=%q,implementation=\"go\"} %d\n", kind, lane.output)
}
