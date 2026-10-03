package worker

import (
	"context"
	"encoding/json"
	"sync"
	"time"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	release "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue/releaseevidence"
	"github.com/jackc/pgx/v5/pgxpool"
)

type hostColdRedisKey struct{}
type hostColdRedisScope struct {
	mu                       sync.Mutex
	active                   bool
	host                     *hostColdPhaseScope
	client                   *queue.Client
	endpointSHA, instanceSHA string
	guard                    func() error
}

func (r *hostColdRedisScope) check(ctx context.Context, host *hostColdPhaseScope) error {
	if r == nil || ctx == nil {
		return errHostPreflight
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if !r.active || r.host != host || ctx.Value(hostColdRedisKey{}) != r || r.client == nil || r.guard == nil || r.guard() != nil {
		return errHostPreflight
	}
	instance, err := r.client.RedisInstanceSHA256(ctx)
	if err != nil || instance != r.instanceSHA {
		return errHostPreflight
	}
	return nil
}

func withHostColdRedisScope(ctx context.Context, host *hostColdPhaseScope, client *queue.Client, endpointSHA string, guard func() error, fn func(context.Context) error) error {
	if ctx == nil || host == nil || client == nil || guard == nil || fn == nil || ctx.Value(hostColdRedisKey{}) != nil || !planPattern.MatchString(endpointSHA) || guard() != nil {
		return errHostPreflight
	}
	instance, err := client.RedisInstanceSHA256(ctx)
	if err != nil {
		return errHostPreflight
	}
	r := &hostColdRedisScope{active: true, host: host, client: client, endpointSHA: endpointSHA, instanceSHA: instance, guard: guard}
	defer func() { r.mu.Lock(); r.active = false; r.mu.Unlock() }()
	scoped := context.WithValue(ctx, hostColdRedisKey{}, r)
	if err := fn(scoped); err != nil {
		return err
	}
	return r.check(scoped, host)
}

// WithSelectedHostColdRedis opens the endpoint derived from fresh selected
// Compose/image/consumer execution and actual live Redis daemon/socket evidence.
// Caller URLs and borrowed clients cannot select this journal connection. The
// private pool is closed on every exit, with incarnation and endpoint readbacks
// around every phase; this is not producer/lease exclusion or runtime admission.
func WithSelectedHostColdRedis(ctx context.Context, pool *pgxpool.Pool, fn func(context.Context) error) error {
	if ctx == nil || fn == nil {
		return errHostPreflight
	}
	s, _ := ctx.Value(hostColdPhaseKey{}).(*hostColdPhaseScope)
	if s == nil {
		return errHostPreflight
	}
	s.mu.Lock()
	if s.check(ctx, pool) != nil || s.redis == nil || ctx.Value(hostColdRedisKey{}) != nil {
		s.mu.Unlock()
		return errHostPreflight
	}
	endpoint, err := s.redis()
	s.mu.Unlock()
	if err != nil || endpoint == nil {
		return errHostPreflight
	}
	url, err := endpoint.ConnectionURL(ctx)
	if err != nil {
		return errHostPreflight
	}
	client, err := queue.Open(url, queue.Settings{LeaseTTL: 600 * time.Second, MaxDomains: 10})
	if err != nil {
		return errHostPreflight
	}
	defer client.Close()
	guard := func() error {
		if endpoint.Check(ctx) != nil {
			return errHostPreflight
		}
		current, err := s.redis()
		if err != nil || current == nil || current.SHA256() != endpoint.SHA256() {
			return errHostPreflight
		}
		return nil
	}
	return withHostColdRedisScope(ctx, s, client, endpoint.SHA256(), guard, func(scoped context.Context) error {
		r := scoped.Value(hostColdRedisKey{}).(*hostColdRedisScope)
		body, err := json.Marshal(struct {
			Version          string                   `json:"version"`
			Binding          queue.HostColdSQLBinding `json:"binding"`
			Endpoint         json.RawMessage          `json:"endpoint"`
			EndpointSHA      string                   `json:"endpoint_sha256"`
			InstanceSHA      string                   `json:"instance_sha256"`
			RuntimeAdmission bool                     `json:"runtime_admission"`
		}{"jobseek.crawler-host-cold-redis/v1", s.info.Binding, json.RawMessage(endpoint.Body()), endpoint.SHA256(), r.instanceSHA, false})
		if err != nil || guard() != nil || s.store.retain("cold-redis-"+hostDigest(body)+".json", body, nil) != nil || guard() != nil {
			return errHostPreflight
		}
		return fn(scoped)
	})
}

type hostRedisResolver func() (*release.RedisEndpoint, error)
