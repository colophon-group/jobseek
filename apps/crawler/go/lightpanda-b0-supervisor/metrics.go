package main

import (
	"context"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var queueOperations = []string{"initialize", "claim_next", "heartbeat", "complete", "reschedule_at", "reap_expired", "audit"}
var queueOutcomes = []string{"accepted", "fenced", "not_current", "transport_error"}

type metrics struct {
	queue    map[string]*atomic.Uint64
	render   [2]atomic.Uint64
	exec     [2]atomic.Uint64
	inflight atomic.Int64
	ready    atomic.Bool
	mu       sync.Mutex
}

func newMetrics() *metrics {
	m := &metrics{queue: make(map[string]*atomic.Uint64, len(queueOperations)*len(queueOutcomes))}
	for _, operation := range queueOperations {
		for _, outcome := range queueOutcomes {
			m.queue[operation+"\x00"+outcome] = &atomic.Uint64{}
		}
	}
	return m
}

func (m *metrics) incQueue(operation, outcome string) {
	if counter := m.queue[operation+"\x00"+outcome]; counter != nil {
		counter.Add(1)
	}
}

func (m *metrics) handler(response http.ResponseWriter, request *http.Request) {
	if request.URL.Path == "/healthz" {
		if !m.ready.Load() {
			http.Error(response, "not ready", http.StatusServiceUnavailable)
			return
		}
		response.WriteHeader(http.StatusNoContent)
		return
	}
	if request.URL.Path != "/metrics" {
		http.NotFound(response, request)
		return
	}
	response.Header().Set("Content-Type", "text/plain; version=0.0.4")
	keys := make([]string, 0, len(m.queue))
	for key := range m.queue {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var body strings.Builder
	for _, key := range keys {
		parts := strings.Split(key, "\x00")
		fmt.Fprintf(&body, "jobseek_lightpanda_b0_queue_transitions_total{operation=%q,outcome=%q} %d\n", parts[0], parts[1], m.queue[key].Load())
	}
	fmt.Fprintf(&body, "jobseek_lightpanda_b0_inflight %d\n", m.inflight.Load())
	fmt.Fprintf(&body, "jobseek_lightpanda_b0_render_total{outcome=\"success\"} %d\n", m.render[0].Load())
	fmt.Fprintf(&body, "jobseek_lightpanda_b0_render_total{outcome=\"failure\"} %d\n", m.render[1].Load())
	fmt.Fprintf(&body, "jobseek_lightpanda_b0_executor_total{outcome=\"committed\"} %d\n", m.exec[0].Load())
	fmt.Fprintf(&body, "jobseek_lightpanda_b0_executor_total{outcome=\"failure\"} %d\n", m.exec[1].Load())
	_, _ = response.Write([]byte(body.String()))
}

func (m *metrics) serve(ctx context.Context, address string) error {
	server := &http.Server{Addr: address, Handler: http.HandlerFunc(m.handler), ReadHeaderTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second, IdleTimeout: 5 * time.Second, MaxHeaderBytes: 4 * 1024}
	done := make(chan error, 1)
	go func() { done <- server.ListenAndServe() }()
	select {
	case err := <-done:
		if err == http.ErrServerClosed {
			return nil
		}
		return err
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		return server.Shutdown(shutdown)
	}
}
