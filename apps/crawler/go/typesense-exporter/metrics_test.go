package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestExporterMetricsPreserveStalenessAndImportSignals(t *testing.T) {
	m := newExporterMetrics()
	health := httptest.NewRecorder()
	m.handler().ServeHTTP(health, httptest.NewRequest(http.MethodGet, "/health", nil))
	if health.Code != http.StatusServiceUnavailable {
		t.Fatalf("unstarted exporter reported healthy: %d", health.Code)
	}
	m.recordImportError(3, 200*time.Millisecond, 5*time.Second)
	m.recordTick(tickResult{Read: 4, Rejected: 1, ImportDuration: 100 * time.Millisecond}, time.Second)
	m.setLag(7)
	m.setRedis(map[string]int64{"ready:simple:0:ready": 2, "ready:simple:0:total": 5}, true)
	m.setTypesenseHealth(true)
	m.recordCDC(4*time.Second, 2, 1, 0)
	health = httptest.NewRecorder()
	m.handler().ServeHTTP(health, httptest.NewRequest(http.MethodGet, "/health", nil))
	if health.Code != http.StatusOK {
		t.Fatalf("successful exporter tick reported unhealthy: %d", health.Code)
	}
	metrics := httptest.NewRecorder()
	m.handler().ServeHTTP(metrics, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	for _, line := range []string{
		"crawler_exporter_rows_exported_total{table=\"job_posting\"} 4",
		"crawler_typesense_export_docs_total{status=\"success\"} 3",
		"crawler_typesense_export_docs_total{status=\"error\"} 4",
		"crawler_export_errors_total{table=\"job_posting\",phase=\"typesense\"} 1",
		"crawler_exporter_cdc_cutoff_delay_seconds 4",
		"crawler_exporter_cdc_active_writers 2",
		"crawler_exporter_cdc_released_writer_races_total 1",
		"crawler_exporter_cdc_unknown_writers_total 0",
		"crawler_typesense_export_lag 7",
		"crawler_redis_connected 1",
		"crawler_redis_queue_depth{queue=\"ready:simple:0:ready\"} 2",
		"crawler_typesense_healthy 1",
		"crawler_exporter_downstream_available{target=\"typesense\"} 1",
		"crawler_exporter_downstream_backoff_seconds{target=\"typesense\"} 0",
		"crawler_exporter_flush_duration_seconds_count 1",
		"crawler_typesense_export_duration_seconds_count 2",
	} {
		if !strings.Contains(metrics.Body.String(), line+"\n") {
			t.Errorf("missing metric %q", line)
		}
	}
}
