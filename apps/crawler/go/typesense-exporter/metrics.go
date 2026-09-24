package main

import (
	"fmt"
	"net/http"
	"sync"
	"time"
)

var flushBuckets = []float64{0.5, 1, 2, 5, 10, 15, 30, 60}
var importBuckets = []float64{0.1, 0.25, 0.5, 1, 2, 5, 10, 30}

// exporterMetrics keeps the existing exporter dashboard and staleness alert
// series available when this binary replaces the Python process on port 9093.
type exporterMetrics struct {
	mu              sync.Mutex
	lastFlush       float64
	rowsExported    uint64
	docsSucceeded   uint64
	docsFailed      uint64
	rowErrors       uint64
	lag             int64
	lagSet          bool
	redisConnected  bool
	queueDepths     map[string]int64
	typesenseHealth bool
	cutoffDelay     float64
	activeWriters   int32
	releasedWriters uint64
	unknownWriters  uint64
	downstreamUp    bool
	backoffSeconds  float64
	flushCount      uint64
	flushSum        float64
	flushBins       []uint64
	importCount     uint64
	importSum       float64
	importBins      []uint64
}

func newExporterMetrics() *exporterMetrics {
	return &exporterMetrics{
		flushBins: make([]uint64, len(flushBuckets)), importBins: make([]uint64, len(importBuckets)),
		queueDepths: make(map[string]int64),
	}
}

func (m *exporterMetrics) recordTick(result tickResult, duration time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.lastFlush = float64(time.Now().Unix())
	m.downstreamUp = true
	m.backoffSeconds = 0
	m.rowsExported += uint64(result.Read)
	m.docsSucceeded += uint64(result.Read - result.Rejected)
	m.docsFailed += uint64(result.Rejected)
	m.rowErrors += uint64(result.Rejected)
	observeHistogram(duration.Seconds(), flushBuckets, m.flushBins, &m.flushCount, &m.flushSum)
	if result.ImportDuration > 0 {
		observeHistogram(result.ImportDuration.Seconds(), importBuckets, m.importBins, &m.importCount, &m.importSum)
	}
}

func (m *exporterMetrics) recordImportError(count int, duration time.Duration, backoff time.Duration) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.downstreamUp = false
	m.backoffSeconds = backoff.Seconds()
	m.docsFailed += uint64(count)
	if duration > 0 {
		observeHistogram(duration.Seconds(), importBuckets, m.importBins, &m.importCount, &m.importSum)
	}
}

func (m *exporterMetrics) setLag(lag int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.lag = lag
	m.lagSet = true
}

func (m *exporterMetrics) setRedis(depths map[string]int64, connected bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.redisConnected = connected
	if connected {
		m.queueDepths = depths
	}
}

func (m *exporterMetrics) setTypesenseHealth(healthy bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.typesenseHealth = healthy
}

func (m *exporterMetrics) recordCDC(delay time.Duration, active, released, unknown int32) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.cutoffDelay = delay.Seconds()
	m.activeWriters = active
	m.releasedWriters += uint64(released)
	m.unknownWriters += uint64(unknown)
}

func observeHistogram(seconds float64, bounds []float64, bins []uint64, count *uint64, sum *float64) {
	*count++
	*sum += seconds
	for i, bound := range bounds {
		if seconds <= bound {
			bins[i]++
		}
	}
}

func (m *exporterMetrics) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		m.mu.Lock()
		lastFlush := m.lastFlush
		m.mu.Unlock()
		if lastFlush == 0 || time.Since(time.Unix(int64(lastFlush), 0)) > 5*time.Minute {
			http.Error(w, "exporter has no recent successful tick", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, _ *http.Request) {
		m.mu.Lock()
		defer m.mu.Unlock()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintln(w, "# TYPE crawler_exporter_last_flush_ts gauge")
		fmt.Fprintf(w, "crawler_exporter_last_flush_ts %g\n", m.lastFlush)
		fmt.Fprintln(w, "# TYPE crawler_exporter_rows_exported_total counter")
		fmt.Fprintf(w, "crawler_exporter_rows_exported_total{table=\"job_posting\"} %d\n", m.rowsExported)
		fmt.Fprintln(w, "# TYPE crawler_typesense_export_docs_total counter")
		fmt.Fprintf(w, "crawler_typesense_export_docs_total{status=\"success\"} %d\n", m.docsSucceeded)
		fmt.Fprintf(w, "crawler_typesense_export_docs_total{status=\"error\"} %d\n", m.docsFailed)
		fmt.Fprintln(w, "# TYPE crawler_export_errors_total counter")
		fmt.Fprintf(w, "crawler_export_errors_total{table=\"job_posting\",phase=\"typesense\"} %d\n", m.rowErrors)
		fmt.Fprintln(w, "# TYPE crawler_exporter_cdc_cutoff_delay_seconds gauge")
		fmt.Fprintf(w, "crawler_exporter_cdc_cutoff_delay_seconds %g\n", m.cutoffDelay)
		fmt.Fprintln(w, "# TYPE crawler_exporter_cdc_active_writers gauge")
		fmt.Fprintf(w, "crawler_exporter_cdc_active_writers %d\n", m.activeWriters)
		fmt.Fprintln(w, "# TYPE crawler_exporter_cdc_released_writer_races_total counter")
		fmt.Fprintf(w, "crawler_exporter_cdc_released_writer_races_total %d\n", m.releasedWriters)
		fmt.Fprintln(w, "# TYPE crawler_exporter_cdc_unknown_writers_total counter")
		fmt.Fprintf(w, "crawler_exporter_cdc_unknown_writers_total %d\n", m.unknownWriters)
		fmt.Fprintln(w, "# TYPE crawler_typesense_export_lag gauge")
		if m.lagSet {
			fmt.Fprintf(w, "crawler_typesense_export_lag %d\n", m.lag)
		}
		fmt.Fprintln(w, "# TYPE crawler_redis_connected gauge")
		redisConnected := 0
		if m.redisConnected {
			redisConnected = 1
		}
		fmt.Fprintf(w, "crawler_redis_connected %d\n", redisConnected)
		fmt.Fprintln(w, "# TYPE crawler_typesense_healthy gauge")
		typesenseHealthy := 0
		if m.typesenseHealth {
			typesenseHealthy = 1
		}
		fmt.Fprintf(w, "crawler_typesense_healthy %d\n", typesenseHealthy)
		fmt.Fprintln(w, "# TYPE crawler_redis_queue_depth gauge")
		for _, key := range readyQueueKeys {
			for _, suffix := range []string{":ready", ":total"} {
				name := key + suffix
				if value, ok := m.queueDepths[name]; ok {
					fmt.Fprintf(w, "crawler_redis_queue_depth{queue=\"%s\"} %d\n", name, value)
				}
			}
		}
		fmt.Fprintln(w, "# TYPE crawler_exporter_downstream_available gauge")
		available := 0
		if m.downstreamUp {
			available = 1
		}
		fmt.Fprintf(w, "crawler_exporter_downstream_available{target=\"typesense\"} %d\n", available)
		fmt.Fprintln(w, "# TYPE crawler_exporter_downstream_backoff_seconds gauge")
		fmt.Fprintf(w, "crawler_exporter_downstream_backoff_seconds{target=\"typesense\"} %g\n", m.backoffSeconds)
		writeHistogram(w, "crawler_exporter_flush_duration_seconds", flushBuckets, m.flushBins, m.flushCount, m.flushSum)
		writeHistogram(w, "crawler_typesense_export_duration_seconds", importBuckets, m.importBins, m.importCount, m.importSum)
	})
	return mux
}

func writeHistogram(w http.ResponseWriter, name string, bounds []float64, bins []uint64, count uint64, sum float64) {
	fmt.Fprintf(w, "# TYPE %s histogram\n", name)
	for i, bound := range bounds {
		fmt.Fprintf(w, "%s_bucket{le=\"%g\"} %d\n", name, bound, bins[i])
	}
	fmt.Fprintf(w, "%s_bucket{le=\"+Inf\"} %d\n", name, count)
	fmt.Fprintf(w, "%s_sum %g\n", name, sum)
	fmt.Fprintf(w, "%s_count %d\n", name, count)
}
