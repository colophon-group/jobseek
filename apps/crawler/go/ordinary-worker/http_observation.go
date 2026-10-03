package worker

import (
	"context"
	"errors"
	"net"
	"sort"
	"strings"
	"sync"
)

type HTTPObservation struct {
	mu       sync.Mutex
	hosts    map[string]bool
	snapshot HTTPSnapshot
}

type HTTPSnapshot struct {
	Hosts                                         []string
	LastHost, LastTransportError                  string
	LastStatus                                    int
	Requests, Responses, NoResponse, EncodedBytes int64
}

type observationContextKey struct{}

func ObserveHTTP(ctx context.Context) (context.Context, *HTTPObservation) {
	observation := &HTTPObservation{hosts: make(map[string]bool)}
	return context.WithValue(ctx, observationContextKey{}, observation), observation
}

func httpObservation(ctx context.Context) *HTTPObservation {
	observation, _ := ctx.Value(observationContextKey{}).(*HTTPObservation)
	return observation
}

func (o *HTTPObservation) Snapshot() HTTPSnapshot {
	o.mu.Lock()
	defer o.mu.Unlock()
	value := o.snapshot
	for host := range o.hosts {
		value.Hosts = append(value.Hosts, host)
	}
	sort.Strings(value.Hosts)
	return value
}

func (o *HTTPObservation) noteRequest(host string) {
	if o == nil {
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	host = strings.TrimRight(strings.ToLower(host), ".")
	o.hosts[host] = true
	o.snapshot.LastHost, o.snapshot.LastStatus, o.snapshot.LastTransportError = host, 0, ""
	o.snapshot.Requests++
}
func (o *HTTPObservation) noteResponse(host string, status int) {
	if o == nil {
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.snapshot.LastHost, o.snapshot.LastStatus, o.snapshot.LastTransportError = strings.TrimRight(strings.ToLower(host), "."), status, ""
	o.snapshot.Responses++
}
func (o *HTTPObservation) noteFailure(host string, err error) {
	if o == nil {
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	kind := "transport_error"
	var failure net.Error
	if errors.Is(err, context.Canceled) {
		kind = "canceled"
	} else if errors.Is(err, context.DeadlineExceeded) || errors.As(err, &failure) && failure.Timeout() {
		kind = "timeout"
	}
	o.snapshot.LastHost, o.snapshot.LastStatus, o.snapshot.LastTransportError = strings.TrimRight(strings.ToLower(host), "."), 0, kind
	o.snapshot.NoResponse++
}
func (o *HTTPObservation) noteBytes(n int) {
	if o == nil {
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.snapshot.EncodedBytes += int64(n)
}
