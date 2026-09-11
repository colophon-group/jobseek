package main

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type fakeHeldReservation struct {
	mu     sync.Mutex
	closed bool
}

func (r *fakeHeldReservation) close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
}

func (r *fakeHeldReservation) execute(context.Context, queueTask) ([]byte, error) {
	return nil, errors.New("unused")
}

func (r *fakeHeldReservation) isClosed() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.closed
}

type failingRendererSource struct {
	mu           sync.Mutex
	calls        int
	reservations []*fakeHeldReservation
}

func (s *failingRendererSource) reserve(context.Context) (heldReservation, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls++
	if s.calls == 2 {
		return nil, errors.New("slot unavailable")
	}
	reservation := &fakeHeldReservation{}
	s.reservations = append(s.reservations, reservation)
	return reservation, nil
}

func TestStartupReservationFailureDrainsAllC4OutcomesAndClosesSuccesses(t *testing.T) {
	source := &failingRendererSource{}
	s := &supervisor{renderer: source}
	reservations, err := s.reserveStartup(context.Background())
	if err == nil || reservations != nil {
		t.Fatalf("partial C4 reservation was accepted: %v", err)
	}
	source.mu.Lock()
	calls := source.calls
	acquired := append([]*fakeHeldReservation(nil), source.reservations...)
	source.mu.Unlock()
	if calls != supervisorCapacity || len(acquired) != supervisorCapacity-1 {
		t.Fatalf("startup did not drain all outcomes: calls=%d acquired=%d", calls, len(acquired))
	}
	for _, reservation := range acquired {
		if !reservation.isClosed() {
			t.Fatal("successful partial reservation leaked")
		}
	}
}

func TestExecutorRequestSnapshotIsSerializedWithHeartbeatMutation(t *testing.T) {
	task := validQueueTask(t)
	authority := &leaseAuthority{lease: &lease{Task: task, ClaimToken: "7:1", LeaseUntilMS: 1}}
	start := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		<-start
		for value := int64(2); value < 10_000; value++ {
			authority.mu.Lock()
			authority.lease.LeaseUntilMS = value
			authority.mu.Unlock()
		}
	}()
	close(start)
	for range 10_000 {
		request := authority.executorRequest([]byte{1})
		if request.LeaseUntilMS < 1 || request.ClaimToken != "7:1" {
			t.Fatal("executor request snapshot was torn")
		}
	}
	<-done
}

type recordingLeaseQueue struct {
	mu             sync.Mutex
	heartbeatCalls int
	terminalCalls  int
}

func (q *recordingLeaseQueue) heartbeat(context.Context, *lease, time.Duration) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.heartbeatCalls++
	return nil
}

func (q *recordingLeaseQueue) terminal(context.Context, *lease, *int64) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.terminalCalls++
	return nil
}

func TestSuccessfulTerminalMarksAuthorityFinished(t *testing.T) {
	queue := &recordingLeaseQueue{}
	authority := &leaseAuthority{
		lease:  &lease{Task: validQueueTask(t), ClaimToken: "7:13", LeaseUntilMS: 20_000},
		queue:  queue,
		config: config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
	}
	if _, err := authority.authorizeAndFinish(context.Background(), func(context.Context, int64) (*int64, error) {
		return nil, nil
	}); err != nil {
		t.Fatal(err)
	}
	finished, err := authority.heartbeatOnce(context.Background())
	if err != nil || !finished {
		t.Fatalf("terminal authority remained heartbeat-eligible: finished=%t err=%v", finished, err)
	}
	queue.mu.Lock()
	heartbeats, terminals := queue.heartbeatCalls, queue.terminalCalls
	queue.mu.Unlock()
	if heartbeats != 1 || terminals != 1 {
		t.Fatalf("unexpected queue transitions: heartbeats=%d terminals=%d", heartbeats, terminals)
	}
}

func TestQueuedHeartbeatDoesNotRunAfterTerminalCompletion(t *testing.T) {
	queue := &recordingLeaseQueue{}
	authority := &leaseAuthority{
		lease:  &lease{Task: validQueueTask(t), ClaimToken: "7:12", LeaseUntilMS: 20_000},
		queue:  queue,
		config: config{LeaseTTL: time.Minute},
	}
	authority.mu.Lock()
	started := make(chan struct{})
	done := make(chan error, 1)
	go func() {
		close(started)
		finished, err := authority.heartbeatOnce(context.Background())
		if err == nil && !finished {
			err = errors.New("heartbeat did not observe terminal completion")
		}
		done <- err
	}()
	<-started
	authority.finished = true
	authority.mu.Unlock()
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	queue.mu.Lock()
	calls := queue.heartbeatCalls
	queue.mu.Unlock()
	if calls != 0 {
		t.Fatalf("heartbeat ran after terminal completion: %d calls", calls)
	}
}
