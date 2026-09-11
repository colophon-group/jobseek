package main

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

const (
	supervisorCapacity = 4
	heartbeatInterval  = 15 * time.Second
	queueTimeout       = 5 * time.Second
	reaperInterval     = 30 * time.Second
	shutdownTimeout    = 15 * time.Second
)

type supervisor struct {
	config   config
	queue    *b0Queue
	renderer rendererSource
	metrics  *metrics
}

type rendererSource interface {
	reserve(context.Context) (heldReservation, error)
}

type heldReservation interface {
	close()
	execute(context.Context, queueTask) ([]byte, error)
}

type leaseQueue interface {
	heartbeat(context.Context, *lease, time.Duration) error
	terminal(context.Context, *lease, *int64) error
}

type leaseAuthority struct {
	mu       sync.Mutex
	lease    *lease
	queue    leaseQueue
	config   config
	finished bool
}

func newSupervisor(c config) (*supervisor, *redis.Client, error) {
	if err := c.validate(); err != nil {
		return nil, nil, err
	}
	m := newMetrics()
	client := redis.NewClient(c.RedisOptions)
	queue, err := newB0Queue(client, c.LuaPath, c.Namespace, c.Route, c.DefaultDelay, m)
	if err != nil {
		_ = client.Close()
		return nil, nil, err
	}
	renderer, err := newRenderer(c)
	if err != nil {
		_ = client.Close()
		return nil, nil, err
	}
	return &supervisor{config: c, queue: queue, renderer: renderer, metrics: m}, client, nil
}

func (s *supervisor) run(ctx context.Context) error {
	reservations, err := s.reserveStartup(ctx)
	if err != nil {
		return err
	}
	defer func() {
		for _, held := range reservations {
			if held != nil {
				held.close()
			}
		}
	}()
	if err := bounded(ctx, queueTimeout, s.queue.initialize); err != nil {
		return err
	}
	if err := bounded(ctx, queueTimeout, func(call context.Context) error { return s.queue.reap(call, 3) }); err != nil {
		return err
	}
	if err := bounded(ctx, queueTimeout, s.queue.audit); err != nil {
		return err
	}
	s.metrics.ready.Store(true)
	defer s.metrics.ready.Store(false)

	runCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	errorsChannel := make(chan error, supervisorCapacity+1)
	var wait sync.WaitGroup
	for index := range supervisorCapacity {
		held := reservations[index]
		reservations[index] = nil
		wait.Add(1)
		go func(initial heldReservation) {
			defer wait.Done()
			if workerErr := s.worker(runCtx, initial); workerErr != nil && !errors.Is(workerErr, context.Canceled) {
				errorsChannel <- workerErr
				cancel()
			}
		}(held)
	}
	wait.Add(1)
	go func() {
		defer wait.Done()
		if reapErr := s.reaper(runCtx); reapErr != nil && !errors.Is(reapErr, context.Canceled) {
			errorsChannel <- reapErr
			cancel()
		}
	}()
	waitDone := make(chan struct{})
	go func() { wait.Wait(); close(waitDone) }()
	select {
	case <-ctx.Done():
		cancel()
	case <-runCtx.Done():
	}
	select {
	case <-waitDone:
	case <-time.After(shutdownTimeout):
		return errors.New("B0 supervisor shutdown exceeded its bound")
	}
	shutdown, shutdownCancel := context.WithTimeout(context.Background(), queueTimeout)
	defer shutdownCancel()
	if err := s.queue.audit(shutdown); err != nil {
		return fmt.Errorf("B0 shutdown conservation audit: %w", err)
	}
	select {
	case fatal := <-errorsChannel:
		return fatal
	default:
		if ctx.Err() != nil {
			return nil
		}
		return errors.New("B0 supervisor stopped unexpectedly")
	}
}

func (s *supervisor) reserveStartup(ctx context.Context) ([]heldReservation, error) {
	type result struct {
		index       int
		reservation heldReservation
		err         error
	}
	outcomes := make(chan result, supervisorCapacity)
	startup, cancel := context.WithCancel(ctx)
	defer cancel()
	for index := range supervisorCapacity {
		go func(slot int) { held, err := s.renderer.reserve(startup); outcomes <- result{slot, held, err} }(index)
	}
	reservations := make([]heldReservation, supervisorCapacity)
	var firstErr error
	for range supervisorCapacity {
		outcome := <-outcomes
		if outcome.err != nil {
			if firstErr == nil {
				firstErr = outcome.err
				cancel()
			}
		} else {
			reservations[outcome.index] = outcome.reservation
		}
	}
	if firstErr != nil {
		for _, held := range reservations {
			if held != nil {
				held.close()
			}
		}
		return nil, fmt.Errorf("reserve all C4 renderer slots before queue claim: %w", firstErr)
	}
	return reservations, nil
}

func (s *supervisor) worker(ctx context.Context, initial heldReservation) error {
	held := initial
	for {
		if err := ctx.Err(); err != nil {
			if held != nil {
				held.close()
			}
			return err
		}
		if held == nil {
			var err error
			held, err = s.renderer.reserve(ctx)
			if err != nil {
				return err
			}
		}
		claimContext, cancel := context.WithTimeout(ctx, queueTimeout)
		current, outcome, err := s.queue.claim(claimContext, s.config.LeaseTTL)
		cancel()
		if err == nil && outcome.Decision == "not_current" && outcome.Reason == "no_work" {
			held.close()
			held = nil
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
				continue
			}
		}
		if err != nil {
			held.close()
			held = nil
			return transitionError("claim_next", outcome, err)
		}
		if current == nil {
			held.close()
			return errors.New("accepted B0 claim had no lease")
		}
		s.metrics.inflight.Add(1)
		processErr, fatal := s.processLease(ctx, held, current)
		s.metrics.inflight.Add(-1)
		held.close()
		held = nil
		if fatal {
			return processErr
		}
	}
}

func (s *supervisor) processLease(parent context.Context, held heldReservation, current *lease) (error, bool) {
	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	authority := &leaseAuthority{lease: current, queue: s.queue, config: s.config}
	heartbeatErr := make(chan error, 1)
	go func() { heartbeatErr <- authority.heartbeatLoop(ctx, cancel) }()
	result, err := held.execute(ctx, current.Task)
	if err != nil {
		s.metrics.render[1].Add(1)
		cancel()
		heartbeat := <-heartbeatErr
		if heartbeat != nil && !errors.Is(heartbeat, context.Canceled) {
			return heartbeat, true
		}
		return err, false
	}
	s.metrics.render[0].Add(1)
	executorContext, executorCancel := context.WithTimeout(ctx, time.Duration(current.Task.Envelope.TimeoutMS)*time.Millisecond+s.config.ExecutorTimeout)
	request := authority.executorRequest(result)
	_, err = runPythonExecutor(executorContext, s.config, request, authority.authorizeAndFinish)
	executorCancel()
	if err != nil {
		s.metrics.exec[1].Add(1)
	} else {
		s.metrics.exec[0].Add(1)
	}
	cancel()
	heartbeat := <-heartbeatErr
	if heartbeat != nil && !errors.Is(heartbeat, context.Canceled) {
		return heartbeat, true
	}
	if err != nil {
		return err, false
	}
	return nil, false
}

func (a *leaseAuthority) executorRequest(browserResult []byte) executorRequest {
	a.mu.Lock()
	defer a.mu.Unlock()
	return executorRequest{
		Version:       "jobseek.lightpanda.executor/v1",
		TaskPayload:   a.lease.Task.Payload,
		PayloadSHA256: a.lease.Task.PayloadSHA256,
		ClaimToken:    a.lease.ClaimToken,
		LeaseUntilMS:  a.lease.LeaseUntilMS,
		BrowserResult: append([]byte(nil), browserResult...),
	}
}

func (a *leaseAuthority) heartbeatLoop(ctx context.Context, cancel context.CancelFunc) error {
	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			finished, err := a.heartbeatOnce(ctx)
			if err != nil {
				cancel()
				return fmt.Errorf("B0 heartbeat lost authority: %w", err)
			}
			if finished {
				return nil
			}
		}
	}
}

func (a *leaseAuthority) heartbeatOnce(ctx context.Context) (bool, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.finished {
		return true, nil
	}
	err := bounded(ctx, queueTimeout, func(call context.Context) error { return a.queue.heartbeat(call, a.lease, a.config.LeaseTTL) })
	return false, err
}

func (a *leaseAuthority) authorizeAndFinish(ctx context.Context, conversation commitConversation) (*int64, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := bounded(ctx, queueTimeout, func(call context.Context) error { return a.queue.heartbeat(call, a.lease, a.config.LeaseTTL) }); err != nil {
		return nil, err
	}
	commitContext, cancel := context.WithTimeout(ctx, a.config.ExecutorTimeout)
	nextReady, err := conversation(commitContext, a.lease.LeaseUntilMS)
	cancel()
	if err != nil {
		return nil, err
	}
	if err := bounded(ctx, queueTimeout, func(call context.Context) error { return a.queue.terminal(call, a.lease, nextReady) }); err != nil {
		return nil, err
	}
	a.finished = true
	return nextReady, nil
}

func (s *supervisor) reaper(ctx context.Context) error {
	ticker := time.NewTicker(reaperInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			if err := bounded(ctx, queueTimeout, func(call context.Context) error { return s.queue.reap(call, 3) }); err != nil {
				return err
			}
			if err := bounded(ctx, queueTimeout, s.queue.audit); err != nil {
				return err
			}
		}
	}
}

func bounded(parent context.Context, timeout time.Duration, operation func(context.Context) error) error {
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	return operation(ctx)
}
