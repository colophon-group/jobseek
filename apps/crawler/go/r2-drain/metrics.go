package main

import (
	"fmt"
	"net/http"
	"sync"
	"time"
)

var uploadBuckets = []float64{0.05, 0.1, 0.25, 0.5, 1, 2, 5}
var retryBuckets = []float64{1, 2.5, 5, 10, 30, 60, 300, 900}

type metrics struct {
	mu          sync.Mutex
	succeeded   uint64
	superseded  uint64
	failed      uint64
	bytes       uint64
	reaped      uint64
	retryReason map[string]uint64
	uploadCount uint64
	uploadSum   float64
	uploadBins  []uint64
	retryCount  uint64
	retrySum    float64
	retryBins   []uint64
}

func newMetrics() *metrics {
	return &metrics{
		retryReason: make(map[string]uint64),
		uploadBins:  make([]uint64, len(uploadBuckets)),
		retryBins:   make([]uint64, len(retryBuckets)),
	}
}

func (m *metrics) observeUpload(duration time.Duration, current bool, bytes int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if current {
		m.succeeded++
		m.bytes += uint64(bytes)
	} else {
		m.superseded++
	}
	seconds := duration.Seconds()
	m.uploadCount++
	m.uploadSum += seconds
	for i, bound := range uploadBuckets {
		if seconds <= bound {
			m.uploadBins[i]++
		}
	}
}

func (m *metrics) observeFailure(reason string, delay time.Duration, scheduled bool) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.failed++
	if !scheduled {
		return
	}
	m.retryReason[reason]++
	seconds := delay.Seconds()
	m.retryCount++
	m.retrySum += seconds
	for i, bound := range retryBuckets {
		if seconds <= bound {
			m.retryBins[i]++
		}
	}
}

func (m *metrics) addReaped(count int64) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.reaped += uint64(count)
}

func (m *metrics) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, _ *http.Request) {
		m.mu.Lock()
		defer m.mu.Unlock()
		w.Header().Set("Content-Type", "text/plain; version=0.0.4")
		fmt.Fprintln(w, "# TYPE crawler_r2_uploaded_total counter")
		fmt.Fprintf(w, "crawler_r2_uploaded_total{status=\"succeeded\"} %d\n", m.succeeded)
		fmt.Fprintf(w, "crawler_r2_uploaded_total{status=\"superseded\"} %d\n", m.superseded)
		fmt.Fprintf(w, "crawler_r2_uploaded_total{status=\"failed\"} %d\n", m.failed)
		fmt.Fprintln(w, "# TYPE crawler_r2_upload_bytes_total counter")
		fmt.Fprintf(w, "crawler_r2_upload_bytes_total %d\n", m.bytes)
		fmt.Fprintln(w, "# TYPE crawler_r2_reaped_orphans_total counter")
		fmt.Fprintf(w, "crawler_r2_reaped_orphans_total %d\n", m.reaped)
		fmt.Fprintln(w, "# TYPE crawler_r2_retry_scheduled_total counter")
		for _, name := range []string{"http_5xx", "http_other", "timeout", "transport", "database", "other"} {
			fmt.Fprintf(w, "crawler_r2_retry_scheduled_total{reason=\"%s\"} %d\n", name, m.retryReason[name])
		}
		fmt.Fprintln(w, "# TYPE crawler_r2_upload_duration_seconds histogram")
		for i, bound := range uploadBuckets {
			fmt.Fprintf(w, "crawler_r2_upload_duration_seconds_bucket{le=\"%g\"} %d\n", bound, m.uploadBins[i])
		}
		fmt.Fprintf(w, "crawler_r2_upload_duration_seconds_bucket{le=\"+Inf\"} %d\n", m.uploadCount)
		fmt.Fprintf(w, "crawler_r2_upload_duration_seconds_sum %g\n", m.uploadSum)
		fmt.Fprintf(w, "crawler_r2_upload_duration_seconds_count %d\n", m.uploadCount)
		fmt.Fprintln(w, "# TYPE crawler_r2_retry_delay_seconds histogram")
		for i, bound := range retryBuckets {
			fmt.Fprintf(w, "crawler_r2_retry_delay_seconds_bucket{le=\"%g\"} %d\n", bound, m.retryBins[i])
		}
		fmt.Fprintf(w, "crawler_r2_retry_delay_seconds_bucket{le=\"+Inf\"} %d\n", m.retryCount)
		fmt.Fprintf(w, "crawler_r2_retry_delay_seconds_sum %g\n", m.retrySum)
		fmt.Fprintf(w, "crawler_r2_retry_delay_seconds_count %d\n", m.retryCount)
	})
	return mux
}
