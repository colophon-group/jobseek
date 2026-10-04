package worker

import (
	"context"
	"errors"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestRuntimeMetricsPreserveBoundedStatusesMetersAndHealth(t *testing.T) {
	m := newRuntimeMetrics(2, time.Second)
	m.ready.Store(true)
	for _, status := range []string{"succeeded", "failed", "publisher_reserved", "gone_pending", "gone", "host_circuit_open", "host_circuit_half_open", "recovered"} {
		m.record(&GreenhouseClaimResult{Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: status}, DiscoveryStarted: status == "succeeded", DiscoveryDuration: 100 * time.Millisecond, Discovered: 7, HTTP: HTTPSnapshot{Requests: 2, Responses: 1, NoResponse: 1, EncodedBytes: 91, Hosts: []string{"never-emit-secret.example"}}}, nil, time.Second)
	}
	m.record(&GreenhouseClaimResult{Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}}, errors.New("never-emit-secret"), time.Second)
	m.record(nil, context.DeadlineExceeded, time.Second)
	w := httptest.NewRecorder()
	m.ServeHTTP(w, httptest.NewRequest("GET", "/metrics", nil))
	text := w.Body.String()
	for _, expected := range []string{`crawler_tasks_total{kind="monitor",status="tdm_reserved"} 1`, `crawler_tasks_total{kind="monitor",status="gone"} 2`, `crawler_tasks_total{kind="monitor",status="unacknowledged"} 1`, `crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 16`, `outcome="response"} 8`, `outcome="transport_error"} 8`, `crawler_runtime_output_items_total{stage="monitor",implementation="go"} 7`, `crawler_runtime_execution_duration_seconds_count{stage="monitor",implementation="go"} 1`, `crawler_worker_heartbeat_timestamp_seconds{worker_id="1"}`} {
		if !strings.Contains(text, expected) {
			t.Fatalf("native metrics lost bounded contract: %s", expected)
		}
	}
	if strings.Contains(text, "never-emit-secret") {
		t.Fatal("upstream diagnostic or host created metric labels")
	}
	health := func() int {
		w := httptest.NewRecorder()
		m.ServeHTTP(w, httptest.NewRequest("GET", "/healthz", nil))
		return w.Code
	}
	if health() != http.StatusNoContent {
		t.Fatal("fresh active process not healthy")
	}
	m.mu.Lock()
	m.lastProgress = time.Now().Add(-2 * time.Second)
	m.mu.Unlock()
	if health() != http.StatusServiceUnavailable {
		t.Fatal("responsive metrics hid stalled claim loop")
	}
}

func TestRuntimeHealthProbeRequiresExactInstalledProcessIdentity(t *testing.T) {
	m := newRuntimeMetrics(1, time.Second)
	m.source = strings.Repeat("a", 40)
	m.plan = strings.Repeat("b", 64)
	m.epoch = 145
	m.ready.Store(true)
	server := httptest.NewServer(m)
	defer server.Close()
	c := RuntimeConfig{source: m.source, plan: m.plan, epoch: m.epoch, metricsAddress: strings.TrimPrefix(server.URL, "http://")}
	if err := CheckHealth(context.Background(), c); err != nil {
		t.Fatal(err)
	}
	c.epoch++
	if err := CheckHealth(context.Background(), c); err == nil {
		t.Fatal("health response from another routing epoch accepted")
	}
	c.epoch--
	c.plan = strings.Repeat("c", 64)
	if err := CheckHealth(context.Background(), c); err == nil {
		t.Fatal("another installed plan accepted")
	}
	c.plan = m.plan
	m.ready.Store(false)
	if err := CheckHealth(context.Background(), c); err == nil {
		t.Fatal("draining process accepted as ready")
	}
}

func TestRuntimeMetricsAttributeDetailsWithoutChangingMonitorTotals(t *testing.T) {
	m := newRuntimeMetrics(1, time.Second)
	m.record(&GreenhouseClaimResult{Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}, HTTP: HTTPSnapshot{Requests: 2, Responses: 2}}, nil, time.Second)
	m.record(&GreenhouseClaimResult{TaskKind: queue.Scrape, Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "succeeded"}, HTTP: HTTPSnapshot{Requests: 3, Responses: 2, NoResponse: 1, EncodedBytes: 123}, DiscoveryStarted: true, DiscoveryDuration: time.Second, Discovered: 1}, nil, 2*time.Second)
	m.record(&GreenhouseClaimResult{TaskKind: queue.Scrape, Settled: true, Cycle: &queue.GreenhouseCycleResult{Status: "unscheduled"}}, nil, time.Second)
	w := httptest.NewRecorder()
	m.ServeHTTP(w, httptest.NewRequest("GET", "/metrics", nil))
	for _, expected := range []string{
		`crawler_tasks_total{kind="monitor",status="succeeded"} 1`,
		`crawler_tasks_total{kind="scrape",status="succeeded"} 1`,
		`crawler_tasks_total{kind="scrape",status="skipped"} 1`,
		`crawler_task_duration_seconds_count{kind="monitor"} 1`,
		`crawler_task_duration_seconds_count{kind="scrape"} 2`,
		`crawler_monitor_duration_seconds_count{profile="simple"} 1`,
		`crawler_scrape_duration_seconds_count{profile="simple"} 2`,
		`crawler_runtime_origin_attempts_total{stage="monitor",execution_class="http",egress="direct"} 2`,
		`crawler_runtime_origin_attempts_total{stage="scrape",execution_class="http",egress="direct"} 3`,
		`crawler_runtime_response_body_bytes_total{stage="scrape",execution_class="http",egress="direct"} 123`,
		`crawler_runtime_executions_total{stage="scrape",implementation="go",outcome="success"} 1`,
		`crawler_runtime_output_items_total{stage="scrape",implementation="go"} 1`,
	} {
		if !strings.Contains(w.Body.String(), expected) {
			t.Fatal("detail metric attribution lost", expected)
		}
	}
}
