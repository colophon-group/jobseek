package main

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEnabledMetricsExposeBoundedCohortSafetyAndLatencySignals(t *testing.T) {
	metrics := newMetrics()
	metrics.readyCount.Store(3)
	metrics.redisInflight.Store(1)
	metrics.deadCount.Store(2)
	metrics.reaped[0].Add(4)
	metrics.reaped[1].Add(1)
	metrics.dueToClaim.observe(250)
	metrics.dueToComplete.observe(2_500)
	metrics.renderWait.observe(50)
	metrics.executorWait.observe(75)
	request := httptest.NewRequest("GET", "/metrics", nil)
	response := httptest.NewRecorder()

	metrics.handler(response, request)

	if response.Code != 200 {
		t.Fatalf("metrics returned HTTP %d", response.Code)
	}
	body := response.Body.String()
	for _, expected := range []string{
		"jobseek_lightpanda_b0_queue_transitions_total{operation=\"claim_next\",outcome=\"fenced\"}",
		"jobseek_lightpanda_b0_queue_ready 3",
		"jobseek_lightpanda_b0_queue_inflight 1",
		"jobseek_lightpanda_b0_queue_dead 2",
		"jobseek_lightpanda_b0_reaped_total{outcome=\"ready\"} 4",
		"jobseek_lightpanda_b0_due_to_claim_seconds_count 1",
		"jobseek_lightpanda_b0_due_to_complete_seconds_count 1",
		"jobseek_lightpanda_b0_renderer_wait_seconds_count 1",
		"jobseek_lightpanda_b0_executor_wait_seconds_count 1",
	} {
		if !strings.Contains(body, expected) {
			t.Fatalf("metrics omitted %q", expected)
		}
	}
}
