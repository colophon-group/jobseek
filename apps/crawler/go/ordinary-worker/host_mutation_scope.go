package worker

import (
	"context"
	"runtime"
	"sync"
	"time"
)

type hostMutationScopeKey struct{}

type hostMutationScope struct {
	mu           sync.Mutex
	lock         *sharedHostLock
	active, busy bool
}

// WithHostMutationScope retains the original deployment flock across in-process
// preflight, cold SQL phases, SQL release and subsequent restoration/readiness
// work. Each phase still verifies its own release, resource and journal inputs.
// This scope supplies exclusion only; it grants no runtime/startup admission.
// It cannot be transferred through files, environment variables or subprocesses.
func WithHostMutationScope(ctx context.Context, fn func(context.Context) error) error {
	if runtime.GOOS != "linux" {
		return errHostPreflight
	}
	return withHostMutationScope(ctx, hostMutationLock, fn)
}

func withHostMutationScope(ctx context.Context, path string, fn func(context.Context) error) (err error) {
	if ctx == nil || ctx.Err() != nil || fn == nil || ctx.Value(hostMutationScopeKey{}) != nil {
		return errHostPreflight
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	lock, err := acquireHostLock(ctx, path)
	if err != nil {
		return errHostPreflight
	}
	s := &hostMutationScope{lock: lock, active: true}
	defer func() {
		s.mu.Lock()
		s.active = false
		s.mu.Unlock()
		if closeErr := lock.Close(); err == nil && closeErr != nil {
			err = errHostPreflight
		}
	}()
	scoped := context.WithValue(ctx, hostMutationScopeKey{}, s)
	if err = fn(scoped); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.busy || s.check(scoped, path) != nil {
		return errHostPreflight
	}
	return nil
}

// Called while holding s.mu. Expired or substituted scopes never fall back to
// acquiring a new lock; a later caller must explicitly begin a new lifecycle.
func (s *hostMutationScope) check(ctx context.Context, path string) error {
	if s == nil || !s.active || ctx == nil || ctx.Err() != nil || ctx.Value(hostMutationScopeKey{}) != s || s.lock == nil || s.lock.path != path || s.lock.verify() != nil {
		return errHostPreflight
	}
	return nil
}

func CheckHostMutationScope(ctx context.Context) error {
	if ctx == nil {
		return errHostPreflight
	}
	s, _ := ctx.Value(hostMutationScopeKey{}).(*hostMutationScope)
	if s == nil {
		return errHostPreflight
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lock == nil {
		return errHostPreflight
	}
	return s.check(ctx, s.lock.path)
}

// A phase borrows the exact original descriptor without duplicating, reopening
// or unlocking it. Only one phase can borrow a scope at a time. Independent
// callers retain the existing acquire/close behavior.
func acquireHostPhaseLock(ctx context.Context, path string) (*sharedHostLock, func(), error) {
	if ctx == nil || ctx.Err() != nil {
		return nil, nil, errHostPreflight
	}
	if value := ctx.Value(hostMutationScopeKey{}); value != nil {
		s, ok := value.(*hostMutationScope)
		if !ok || s == nil {
			return nil, nil, errHostPreflight
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.busy || s.check(ctx, path) != nil {
			return nil, nil, errHostPreflight
		}
		s.busy = true
		var once sync.Once
		release := func() { once.Do(func() { s.mu.Lock(); s.busy = false; s.mu.Unlock() }) }
		return s.lock, release, nil
	}
	lock, err := acquireHostLock(ctx, path)
	if err != nil {
		return nil, nil, errHostPreflight
	}
	return lock, func() { _ = lock.Close() }, nil
}
