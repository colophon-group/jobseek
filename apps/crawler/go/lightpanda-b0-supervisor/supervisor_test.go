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
	result []byte
	err    error
}

func (r *fakeHeldReservation) close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
}

func (r *fakeHeldReservation) execute(context.Context, queueTask) ([]byte, error) {
	return r.result, r.err
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
	failCalls      int
	heartbeatErr   error
	terminalErr    error
	failErr        error
	failedReadyAt  int64
	terminalReady  *int64
}

func (q *recordingLeaseQueue) heartbeat(context.Context, *lease, time.Duration) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.heartbeatCalls++
	return q.heartbeatErr
}

func (q *recordingLeaseQueue) terminal(_ context.Context, _ *lease, readyAt *int64) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.terminalCalls++
	if readyAt != nil {
		copied := *readyAt
		q.terminalReady = &copied
	}
	return q.terminalErr
}

func (q *recordingLeaseQueue) fail(_ context.Context, _ *lease, readyAtMS int64) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.failCalls++
	q.failedReadyAt = readyAtMS
	return q.failErr
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

func TestRendererFailureIsExplicitlyRescheduled(t *testing.T) {
	queue := &recordingLeaseQueue{}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:21", LeaseUntilMS: 20_000}
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
	}
	err, fatal := s.processLease(context.Background(), &fakeHeldReservation{err: errors.New("render failed")}, current)
	if err == nil || fatal {
		t.Fatalf("renderer failure outcome: err=%v fatal=%t", err, fatal)
	}
	queue.mu.Lock()
	heartbeats, failures, readyAt := queue.heartbeatCalls, queue.failCalls, queue.failedReadyAt
	queue.mu.Unlock()
	if heartbeats != 1 || failures != 1 || readyAt != 20_000 {
		t.Fatalf("renderer failure was not explicitly rescheduled: heartbeats=%d failures=%d ready=%d", heartbeats, failures, readyAt)
	}
}

func TestExecutorFailureIsExplicitlyRescheduled(t *testing.T) {
	queue := &recordingLeaseQueue{}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:22", LeaseUntilMS: 20_000}
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
		executor: func(context.Context, config, executorRequest, authorizeCommit) (*int64, error) {
			return nil, errors.New("executor failed")
		},
	}
	err, fatal := s.processLease(context.Background(), &fakeHeldReservation{result: []byte{1}}, current)
	if err == nil || fatal {
		t.Fatalf("executor failure outcome: err=%v fatal=%t", err, fatal)
	}
	queue.mu.Lock()
	heartbeats, failures := queue.heartbeatCalls, queue.failCalls
	queue.mu.Unlock()
	if heartbeats != 1 || failures != 1 {
		t.Fatalf("executor failure was not explicitly rescheduled: heartbeats=%d failures=%d", heartbeats, failures)
	}
}

func TestExecutorAuthorityLossStopsWithoutAnotherLeaseMutation(t *testing.T) {
	queue := &recordingLeaseQueue{}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:25", LeaseUntilMS: 20_000}
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
		executor: func(context.Context, config, executorRequest, authorizeCommit) (*int64, error) {
			return nil, &authorityError{operation: "postgres-write-fence", err: errExecutorAuthorityLost}
		},
	}
	err, fatal := s.processLease(context.Background(), &fakeHeldReservation{result: []byte{1}}, current)
	if err == nil || !fatal {
		t.Fatalf("executor authority loss did not stop: err=%v fatal=%t", err, fatal)
	}
	queue.mu.Lock()
	heartbeats, failures, terminals := queue.heartbeatCalls, queue.failCalls, queue.terminalCalls
	queue.mu.Unlock()
	if heartbeats != 0 || failures != 0 || terminals != 0 {
		t.Fatalf("authority loss performed another lease mutation: heartbeats=%d failures=%d terminals=%d", heartbeats, failures, terminals)
	}
}

func TestCancellationReleasesInflightLeaseBeforeWorkerExit(t *testing.T) {
	queue := &recordingLeaseQueue{}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:26", LeaseUntilMS: 100_000}
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err, fatal := s.processLease(ctx, &fakeHeldReservation{err: context.Canceled}, current)
	if err == nil || fatal {
		t.Fatalf("cancelled lease did not exit after bounded release: err=%v fatal=%t", err, fatal)
	}
	queue.mu.Lock()
	heartbeats, failures, terminals, readyAt := queue.heartbeatCalls, queue.failCalls, queue.terminalCalls, queue.terminalReady
	queue.mu.Unlock()
	if heartbeats != 0 || failures != 0 || terminals != 1 || readyAt == nil || *readyAt != 40_000 {
		t.Fatalf("cancelled lease was not made immediately ready: heartbeats=%d failures=%d terminals=%d ready=%v", heartbeats, failures, terminals, readyAt)
	}
}

func TestLostExecutorAcknowledgementReschedulesForFenceReplay(t *testing.T) {
	queue := &recordingLeaseQueue{}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:23", LeaseUntilMS: 20_000}
	committed := false
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
		executor: func(ctx context.Context, _ config, _ executorRequest, authorize authorizeCommit) (*int64, error) {
			_, err := authorize(ctx, func(context.Context, int64) (*int64, error) {
				committed = true
				return nil, errors.New("commit acknowledgement lost")
			})
			return nil, err
		},
	}
	err, fatal := s.processLease(context.Background(), &fakeHeldReservation{result: []byte{1}}, current)
	if !committed || err == nil || fatal {
		t.Fatalf("lost acknowledgement did not enter replay path: committed=%t err=%v fatal=%t", committed, err, fatal)
	}
	queue.mu.Lock()
	heartbeats, failures, terminals := queue.heartbeatCalls, queue.failCalls, queue.terminalCalls
	queue.mu.Unlock()
	if heartbeats != 2 || failures != 1 || terminals != 0 {
		t.Fatalf("unexpected replay transitions: heartbeats=%d failures=%d terminals=%d", heartbeats, failures, terminals)
	}
}

func TestUnavailableTerminalFenceStopsInsteadOfMutatingLeaseAgain(t *testing.T) {
	queue := &recordingLeaseQueue{terminalErr: errors.New("redis unavailable")}
	current := &lease{Task: validQueueTask(t), ClaimToken: "7:24", LeaseUntilMS: 20_000}
	s := &supervisor{
		config:         config{LeaseTTL: time.Minute, ExecutorTimeout: time.Second},
		authorityQueue: queue,
		metrics:        newMetrics(),
		executor: func(ctx context.Context, _ config, _ executorRequest, authorize authorizeCommit) (*int64, error) {
			return authorize(ctx, func(context.Context, int64) (*int64, error) { return nil, nil })
		},
	}
	err, fatal := s.processLease(context.Background(), &fakeHeldReservation{result: []byte{1}}, current)
	if err == nil || !fatal {
		t.Fatalf("terminal authority loss did not stop: err=%v fatal=%t", err, fatal)
	}
	queue.mu.Lock()
	failures, terminals := queue.failCalls, queue.terminalCalls
	queue.mu.Unlock()
	if failures != 0 || terminals != 1 {
		t.Fatalf("terminal authority loss performed another lease mutation: failures=%d terminals=%d", failures, terminals)
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
