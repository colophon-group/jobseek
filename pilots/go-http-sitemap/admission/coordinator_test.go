package admission

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/boundedhttp"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/sitemap"
	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/worker"
)

type fakeHandle struct {
	fence Fence
	lost  context.Context
	lose  context.CancelFunc
	stop  chan struct{}
	once  sync.Once

	joinErr error
	order   func(string)
	stops   atomic.Int64
	joins   atomic.Int64
}

func newFakeHandle(fence Fence) *fakeHandle {
	lost, lose := context.WithCancel(context.Background())
	return &fakeHandle{
		fence: fence,
		lost:  lost,
		lose:  lose,
		stop:  make(chan struct{}),
	}
}

func (handle *fakeHandle) Fence() Fence          { return handle.fence }
func (handle *fakeHandle) Lost() context.Context { return handle.lost }
func (handle *fakeHandle) loseLease()            { handle.lose() }

func (handle *fakeHandle) Stop() {
	handle.stops.Add(1)
	if handle.order != nil {
		handle.order("stop")
	}
	handle.once.Do(func() { close(handle.stop) })
}

func (handle *fakeHandle) Join(ctx context.Context) error {
	handle.joins.Add(1)
	if handle.order != nil {
		handle.order("join")
	}
	select {
	case <-handle.lost.Done():
		return ErrLeaseLost
	case <-handle.stop:
		return handle.joinErr
	case <-ctx.Done():
		return ctx.Err()
	}
}

type blockingJoinHandle struct{ *fakeHandle }

func (handle *blockingJoinHandle) Join(ctx context.Context) error {
	handle.joins.Add(1)
	<-ctx.Done()
	return ctx.Err()
}

type panicInspectHandle struct{ *fakeHandle }

func (handle *panicInspectHandle) Fence() Fence {
	panic("secret inspect panic")
}

type lossAtJoinHandle struct{ *fakeHandle }

func (handle *lossAtJoinHandle) Join(ctx context.Context) error {
	select {
	case <-handle.stop:
		handle.loseLease()
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

type fakeDependencies struct {
	mu sync.Mutex

	claims     int
	starts     int
	executions int
	terminals  int
	handles    map[string]*fakeHandle
	order      []string

	claimHook    func(context.Context, Candidate) (ClaimGrant, error)
	startHook    func(context.Context, ClaimGrant) (LeaseHandle, error)
	executeHook  func(context.Context, Candidate, Fence) (Execution, error)
	terminalHook func(context.Context, Candidate, Execution, error) (Fence, error)
}

type realSitemapExecutor struct{ client *boundedhttp.Client }

func (executor realSitemapExecutor) Execute(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
	runner, err := sitemap.New(executor.client, candidate.Sitemap())
	if err != nil {
		return Execution{Fence: fence}, err
	}
	result, err := runner.Run(ctx)
	return Execution{Fence: fence, Result: result}, err
}

func newFakeDependencies() *fakeDependencies {
	return &fakeDependencies{handles: make(map[string]*fakeHandle)}
}

func (fake *fakeDependencies) appendOrder(event string) {
	fake.mu.Lock()
	fake.order = append(fake.order, event)
	fake.mu.Unlock()
}

func (fake *fakeDependencies) Claim(ctx context.Context, candidate Candidate) (ClaimGrant, error) {
	fake.mu.Lock()
	fake.claims++
	fake.order = append(fake.order, "claim")
	hook := fake.claimHook
	fake.mu.Unlock()
	if hook != nil {
		return hook(ctx, candidate)
	}
	return fixtureGrant(candidate, "token-"+candidate.TaskID()), nil
}

func (fake *fakeDependencies) Start(ctx context.Context, grant ClaimGrant) (LeaseHandle, error) {
	fake.mu.Lock()
	fake.starts++
	fake.order = append(fake.order, "start")
	hook := fake.startHook
	fake.mu.Unlock()
	if hook != nil {
		return hook(ctx, grant)
	}
	handle := newFakeHandle(grant.Fence)
	fake.mu.Lock()
	fake.handles[grant.Fence.TaskID] = handle
	fake.mu.Unlock()
	return handle, nil
}

func (fake *fakeDependencies) Execute(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
	fake.mu.Lock()
	fake.executions++
	fake.order = append(fake.order, "execute")
	hook := fake.executeHook
	fake.mu.Unlock()
	if hook != nil {
		return hook(ctx, candidate, fence)
	}
	return Execution{Fence: fence, Result: sitemap.Result{URLs: []string{"https://result.example/job"}}}, nil
}

func (fake *fakeDependencies) Terminal(ctx context.Context, candidate Candidate, execution Execution, executionErr error) (Fence, error) {
	fake.mu.Lock()
	fake.terminals++
	fake.order = append(fake.order, "terminal")
	hook := fake.terminalHook
	fake.mu.Unlock()
	if hook != nil {
		return hook(ctx, candidate, execution, executionErr)
	}
	return execution.Fence, nil
}

func (fake *fakeDependencies) counts() (int, int, int, int) {
	fake.mu.Lock()
	defer fake.mu.Unlock()
	return fake.claims, fake.starts, fake.executions, fake.terminals
}

func fixtureFence(candidate Candidate, token string) Fence {
	opaqueDigest := sha256.Sum256([]byte("opaque-runtime-fence:" + candidate.TaskID()))
	return Fence{
		TaskID:         candidate.TaskID(),
		BoardID:        candidate.Manifest().BoardID,
		ShardID:        "shard-1",
		RoutingEpoch:   3,
		EngineOwner:    "go",
		ConfigRevision: candidate.Manifest().ConfigRevision,
		ClaimToken:     token,
		LeaseID:        "lease-" + candidate.TaskID(),
		FenceDigest:    opaqueDigest,
	}
}

func fixtureGrant(candidate Candidate, token string) ClaimGrant {
	return ClaimGrant{
		Fence:            fixtureFence(candidate, token),
		CandidateDigest:  candidate.Digest(),
		ServerTimeMS:     1_000,
		LeaseUntilMS:     11_000,
		RequestStartedAt: time.Now(),
	}
}

func testConfig(workers, capacity, results, perOrigin int, timeout time.Duration) Config {
	return Config{
		Worker: worker.Config{
			Workers:              workers,
			Capacity:             capacity,
			ResultCapacity:       results,
			PerOriginConcurrency: perOrigin,
			JobTimeout:           timeout,
			ShutdownGrace:        200 * time.Millisecond,
		},
		JoinTimeout: 100 * time.Millisecond,
	}
}

func newTestCoordinator(t *testing.T, config Config, fake *fakeDependencies) *Coordinator {
	t.Helper()
	coordinator, err := New(config, fake, fake, fake, fake)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return coordinator
}

func awaitResult(t *testing.T, coordinator *Coordinator) worker.Result {
	t.Helper()
	select {
	case result, ok := <-coordinator.Results():
		if !ok {
			t.Fatal("results closed early")
		}
		return result
	case <-time.After(2 * time.Second):
		t.Fatal("timed out waiting for result")
		return worker.Result{}
	}
}

func shutdownCoordinator(t *testing.T, coordinator *Coordinator) {
	t.Helper()
	coordinator.Close()
	for range coordinator.Results() {
	}
	select {
	case <-coordinator.Done():
	case <-time.After(2 * time.Second):
		t.Fatal("coordinator did not stop")
	}
}

func TestClaimStartsOnlyAfterWorkerAndOriginDispatch(t *testing.T) {
	fake := newFakeDependencies()
	started := make(chan string, 3)
	releases := map[string]chan struct{}{
		"a-1": make(chan struct{}),
		"a-2": make(chan struct{}),
		"b-1": make(chan struct{}),
	}
	fake.claimHook = func(_ context.Context, candidate Candidate) (ClaimGrant, error) {
		started <- candidate.TaskID()
		return fixtureGrant(candidate, "token-"+candidate.TaskID()), nil
	}
	fake.executeHook = func(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
		select {
		case <-releases[candidate.TaskID()]:
			return Execution{Fence: fence}, nil
		case <-ctx.Done():
			return Execution{Fence: fence}, ctx.Err()
		}
	}
	coordinator := newTestCoordinator(t, testConfig(2, 4, 4, 1, time.Second), fake)

	for _, candidate := range []Candidate{
		fixtureCandidate(t, "a-1", "board-a1", "https://a.example/one.xml"),
		fixtureCandidate(t, "a-2", "board-a2", "https://a.example/two.xml"),
		fixtureCandidate(t, "b-1", "board-b1", "https://b.example/one.xml"),
	} {
		if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
			t.Fatalf("Submit %s: %v", candidate.TaskID(), err)
		}
	}

	first := awaitString(t, started, "first claim")
	second := awaitString(t, started, "second claim")
	seen := map[string]bool{first: true, second: true}
	if !seen["b-1"] || seen["a-1"] == seen["a-2"] {
		t.Fatalf("origin slot did not gate claim: first claims %q, %q", first, second)
	}
	assertNoString(t, started, "queued same-origin claim")

	close(releases["b-1"])
	if result := awaitResult(t, coordinator); result.JobID != "b-1" || result.Err != nil {
		t.Fatalf("unexpected b result: %#v", result)
	}
	assertNoString(t, started, "same-origin claim after only b released")
	activeA, queuedA := "a-1", "a-2"
	if seen["a-2"] {
		activeA, queuedA = queuedA, activeA
	}
	close(releases[activeA])
	if result := awaitResult(t, coordinator); result.JobID != activeA || result.Err != nil {
		t.Fatalf("unexpected active a result: %#v", result)
	}
	select {
	case task := <-started:
		if task != queuedA {
			t.Fatalf("claimed %q, want %q", task, queuedA)
		}
	case <-time.After(time.Second):
		t.Fatal("queued origin task was not claimed")
	}
	close(releases[queuedA])
	if result := awaitResult(t, coordinator); result.JobID != queuedA || result.Err != nil {
		t.Fatalf("unexpected queued a result: %#v", result)
	}
	shutdownCoordinator(t, coordinator)
	assertCounts(t, fake, 3, 3, 3, 3)
}

func TestClaimFenceMismatchSuppressesEveryLaterPhase(t *testing.T) {
	mutations := map[string]func(*ClaimGrant){
		"task":             func(grant *ClaimGrant) { grant.Fence.TaskID += "x" },
		"board":            func(grant *ClaimGrant) { grant.Fence.BoardID += "x" },
		"shard":            func(grant *ClaimGrant) { grant.Fence.ShardID = "" },
		"epoch":            func(grant *ClaimGrant) { grant.Fence.RoutingEpoch = 0 },
		"owner":            func(grant *ClaimGrant) { grant.Fence.EngineOwner = "python" },
		"revision":         func(grant *ClaimGrant) { grant.Fence.ConfigRevision += "x" },
		"token":            func(grant *ClaimGrant) { grant.Fence.ClaimToken = "" },
		"lease id":         func(grant *ClaimGrant) { grant.Fence.LeaseID = "" },
		"candidate digest": func(grant *ClaimGrant) { grant.CandidateDigest[0]++ },
		"server time":      func(grant *ClaimGrant) { grant.ServerTimeMS = 0 },
		"lease expiry":     func(grant *ClaimGrant) { grant.LeaseUntilMS = grant.ServerTimeMS },
		"request start":    func(grant *ClaimGrant) { grant.RequestStartedAt = time.Time{} },
		"digest":           func(grant *ClaimGrant) { grant.Fence.FenceDigest = [sha256.Size]byte{} },
	}
	for name, mutate := range mutations {
		t.Run(name, func(t *testing.T) {
			fake := newFakeDependencies()
			fake.claimHook = func(_ context.Context, candidate Candidate) (ClaimGrant, error) {
				grant := fixtureGrant(candidate, "token")
				mutate(&grant)
				return grant, nil
			}
			coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
			candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
			if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
				t.Fatal(err)
			}
			if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrFenceMismatch) {
				t.Fatalf("got %v", result.Err)
			}
			shutdownCoordinator(t, coordinator)
			assertCounts(t, fake, 1, 0, 0, 0)
		})
	}
}

func TestEchoMismatchFailsClosedAtEachBoundary(t *testing.T) {
	tests := map[string]func(*fakeDependencies){
		"supervisor": func(fake *fakeDependencies) {
			fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
				changed := grant.Fence
				changed.ClaimToken += "x"
				return newFakeHandle(changed), nil
			}
		},
		"executor": func(fake *fakeDependencies) {
			fake.executeHook = func(_ context.Context, _ Candidate, fence Fence) (Execution, error) {
				changed := fence
				changed.LeaseID += "x"
				return Execution{Fence: changed}, nil
			}
		},
		"terminal": func(fake *fakeDependencies) {
			fake.terminalHook = func(_ context.Context, _ Candidate, execution Execution, _ error) (Fence, error) {
				changed := execution.Fence
				changed.RoutingEpoch++
				return changed, nil
			}
		},
	}
	for name, configure := range tests {
		t.Run(name, func(t *testing.T) {
			fake := newFakeDependencies()
			configure(fake)
			coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
			candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
			if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
				t.Fatal(err)
			}
			if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrFenceMismatch) {
				t.Fatalf("got %v", result.Err)
			}
			shutdownCoordinator(t, coordinator)
			_, _, executions, terminals := fake.counts()
			wantExecutions, wantTerminals := 1, 0
			if name == "supervisor" {
				wantExecutions = 0
			}
			if name == "terminal" {
				wantTerminals = 1
			}
			if executions != wantExecutions || terminals != wantTerminals {
				t.Fatalf("executions=%d terminals=%d", executions, terminals)
			}
		})
	}
}

func TestLeaseLossCancelsOnlyItsExecutionAndSuppressesTerminal(t *testing.T) {
	fake := newFakeDependencies()
	started := make(chan string, 2)
	releaseHealthy := make(chan struct{})
	fake.executeHook = func(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
		started <- candidate.TaskID()
		if candidate.TaskID() == "lost" {
			<-ctx.Done()
			return Execution{Fence: fence}, ctx.Err()
		}
		select {
		case <-releaseHealthy:
			return Execution{Fence: fence, Result: sitemap.Result{URLs: []string{"https://healthy.example/job"}}}, nil
		case <-ctx.Done():
			return Execution{Fence: fence}, ctx.Err()
		}
	}
	coordinator := newTestCoordinator(t, testConfig(2, 2, 2, 1, time.Second), fake)
	for _, candidate := range []Candidate{
		fixtureCandidate(t, "lost", "board-lost", "https://lost.example/sitemap.xml"),
		fixtureCandidate(t, "healthy", "board-healthy", "https://healthy.example/sitemap.xml"),
	} {
		if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
			t.Fatal(err)
		}
	}
	_ = awaitString(t, started, "first execution")
	_ = awaitString(t, started, "second execution")
	fake.mu.Lock()
	lostHandle := fake.handles["lost"]
	fake.mu.Unlock()
	if lostHandle == nil {
		t.Fatal("lost handle was not registered")
	}
	lostHandle.loseLease()
	close(releaseHealthy)
	results := map[string]worker.Result{}
	for range 2 {
		result := awaitResult(t, coordinator)
		results[result.JobID] = result
	}
	if !errors.Is(results["lost"].Err, ErrLeaseLost) {
		t.Fatalf("lost result: %v", results["lost"].Err)
	}
	if results["healthy"].Err != nil {
		t.Fatalf("healthy result: %v", results["healthy"].Err)
	}
	shutdownCoordinator(t, coordinator)
	_, _, executions, terminals := fake.counts()
	if executions != 2 || terminals != 1 {
		t.Fatalf("loss was not isolated: executions=%d terminals=%d", executions, terminals)
	}
}

func TestSupervisorStopsAndJoinsBeforeTerminal(t *testing.T) {
	fake := newFakeDependencies()
	handleReady := make(chan *fakeHandle, 1)
	fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
		handle := newFakeHandle(grant.Fence)
		handle.order = fake.appendOrder
		handleReady <- handle
		return handle, nil
	}
	fake.executeHook = func(_ context.Context, _ Candidate, fence Fence) (Execution, error) {
		fake.appendOrder("execute-return")
		return Execution{Fence: fence}, nil
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); result.Err != nil {
		t.Fatal(result.Err)
	}
	handle := awaitValue(t, handleReady, "ordered handle")
	if handle.stops.Load() != 1 || handle.joins.Load() != 1 {
		t.Fatalf("cleanup counts: stop=%d join=%d", handle.stops.Load(), handle.joins.Load())
	}
	fake.mu.Lock()
	order := append([]string(nil), fake.order...)
	fake.mu.Unlock()
	want := []string{"claim", "start", "execute", "execute-return", "stop", "join", "terminal"}
	if fmt.Sprint(order) != fmt.Sprint(want) {
		t.Fatalf("order %v, want %v", order, want)
	}
	shutdownCoordinator(t, coordinator)
}

func TestDuplicateTaskIsRejectedOnlyByInjectedClaimCAS(t *testing.T) {
	fake := newFakeDependencies()
	release := make(chan struct{})
	started := make(chan struct{})
	duplicateClaim := errors.New("duplicate claim rejected")
	var claimed atomic.Bool
	fake.claimHook = func(_ context.Context, candidate Candidate) (ClaimGrant, error) {
		if !claimed.CompareAndSwap(false, true) {
			return ClaimGrant{}, duplicateClaim
		}
		return fixtureGrant(candidate, "only-token"), nil
	}
	fake.executeHook = func(ctx context.Context, _ Candidate, fence Fence) (Execution, error) {
		close(started)
		select {
		case <-release:
			return Execution{Fence: fence}, nil
		case <-ctx.Done():
			return Execution{Fence: fence}, ctx.Err()
		}
	}
	coordinator := newTestCoordinator(t, testConfig(1, 2, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, started, "first execution")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatalf("local duplicate should reach claim CAS: %v", err)
	}
	close(release)
	var successes, rejected int
	for range 2 {
		result := awaitResult(t, coordinator)
		switch {
		case result.Err == nil:
			successes++
		case errors.Is(result.Err, duplicateClaim):
			rejected++
		default:
			t.Fatalf("unexpected result: %v", result.Err)
		}
	}
	shutdownCoordinator(t, coordinator)
	if successes != 1 || rejected != 1 {
		t.Fatalf("successes=%d rejected=%d", successes, rejected)
	}
	assertCounts(t, fake, 2, 1, 1, 1)
}

func TestCloseGateWaitsForStartedClaimAndSuppressesLaterClaims(t *testing.T) {
	fake := newFakeDependencies()
	claimEntered := make(chan struct{})
	claimExited := make(chan struct{})
	fake.claimHook = func(ctx context.Context, candidate Candidate) (ClaimGrant, error) {
		close(claimEntered)
		<-ctx.Done()
		close(claimExited)
		return ClaimGrant{}, ctx.Err()
	}
	coordinator := newTestCoordinator(t, testConfig(1, 2, 2, 1, time.Second), fake)
	first := fixtureCandidate(t, "first", "board-first", "https://one.example/first.xml")
	queued := fixtureCandidate(t, "queued", "board-queued", "https://two.example/queued.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), first); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, claimEntered, "claim entry")
	if err := coordinator.Submit(context.Background(), context.Background(), queued); err != nil {
		t.Fatal(err)
	}
	closed := make(chan struct{})
	go func() {
		coordinator.Close()
		close(closed)
	}()
	select {
	case <-claimExited:
	case <-time.After(time.Second):
		t.Fatal("started claim did not observe Close cancellation")
	}
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("Close did not finish after claim exit")
	}
	postClose := fixtureCandidate(t, "post", "board-post", "https://three.example/post.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), postClose); !errors.Is(err, ErrClosed) {
		t.Fatalf("post-close submit got %v", err)
	}
	results := map[string]worker.Result{}
	for range 2 {
		result := awaitResult(t, coordinator)
		results[result.JobID] = result
	}
	if !errors.Is(results["first"].Err, ErrClosed) || !errors.Is(results["queued"].Err, ErrClosed) {
		t.Fatalf("close results: first=%v queued=%v", results["first"].Err, results["queued"].Err)
	}
	awaitSignal(t, coordinator.Done(), "closed coordinator")
	assertCounts(t, fake, 1, 0, 0, 0)
}

func TestShutdownDeadlineBoundsNoncooperativeClaim(t *testing.T) {
	fake := newFakeDependencies()
	entered := make(chan struct{})
	release := make(chan struct{})
	fake.claimHook = func(_ context.Context, _ Candidate) (ClaimGrant, error) {
		close(entered)
		<-release
		return ClaimGrant{}, ErrClosed
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, entered, "noncooperative claim entry")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	started := time.Now()
	if err := coordinator.Shutdown(shutdownCtx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("Shutdown got %v", err)
	}
	if time.Since(started) > 200*time.Millisecond {
		t.Fatal("Shutdown deadline was hidden behind blocking Close")
	}
	close(release)
	for range coordinator.Results() {
	}
	select {
	case <-coordinator.Done():
	case <-time.After(time.Second):
		t.Fatal("coordinator did not drain after dependency released")
	}
}

func TestAdmissionContextDoesNotOwnAcceptedExecution(t *testing.T) {
	fake := newFakeDependencies()
	executed := make(chan error, 1)
	fake.executeHook = func(ctx context.Context, _ Candidate, fence Fence) (Execution, error) {
		select {
		case <-ctx.Done():
			executed <- ctx.Err()
			return Execution{Fence: fence}, ctx.Err()
		case <-time.After(30 * time.Millisecond):
			executed <- nil
			return Execution{Fence: fence}, nil
		}
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	admissionCtx, cancelAdmission := context.WithCancel(context.Background())
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(admissionCtx, context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	cancelAdmission()
	if result := awaitResult(t, coordinator); result.Err != nil {
		t.Fatalf("admission cancellation escaped into execution: %v", result.Err)
	}
	if err := awaitValue(t, executed, "execution outcome"); err != nil {
		t.Fatalf("execution context was canceled: %v", err)
	}
	shutdownCoordinator(t, coordinator)
}

func TestQueuedExecutionCancellationNeverClaims(t *testing.T) {
	fake := newFakeDependencies()
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	fake.executeHook = func(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
		if candidate.TaskID() == "first" {
			close(firstStarted)
			select {
			case <-releaseFirst:
				return Execution{Fence: fence}, nil
			case <-ctx.Done():
				return Execution{Fence: fence}, ctx.Err()
			}
		}
		return Execution{Fence: fence}, nil
	}
	coordinator := newTestCoordinator(t, testConfig(1, 2, 2, 1, time.Second), fake)
	first := fixtureCandidate(t, "first", "board-first", "https://one.example/first.xml")
	queued := fixtureCandidate(t, "queued", "board-queued", "https://two.example/queued.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), first); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, firstStarted, "first execution")
	queuedCtx, cancelQueued := context.WithCancel(context.Background())
	if err := coordinator.Submit(context.Background(), queuedCtx, queued); err != nil {
		t.Fatal(err)
	}
	cancelQueued()
	close(releaseFirst)
	results := map[string]worker.Result{}
	for range 2 {
		result := awaitResult(t, coordinator)
		results[result.JobID] = result
	}
	if results["first"].Err != nil || !errors.Is(results["queued"].Err, context.Canceled) {
		t.Fatalf("results: first=%v queued=%v", results["first"].Err, results["queued"].Err)
	}
	shutdownCoordinator(t, coordinator)
	assertCounts(t, fake, 1, 1, 1, 1)
}

func TestCancellationAtTerminalStartSuppressesMutation(t *testing.T) {
	fake := newFakeDependencies()
	terminalStarted := make(chan struct{})
	var applied atomic.Bool
	fake.terminalHook = func(ctx context.Context, _ Candidate, execution Execution, _ error) (Fence, error) {
		close(terminalStarted)
		<-ctx.Done()
		if ctx.Err() == nil {
			applied.Store(true)
		}
		return execution.Fence, ctx.Err()
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	executionCtx, cancelExecution := context.WithCancel(context.Background())
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), executionCtx, candidate); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, terminalStarted, "terminal entry")
	cancelExecution()
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, context.Canceled) {
		t.Fatalf("got %v", result.Err)
	}
	if applied.Load() {
		t.Fatal("terminal mutation applied after cancellation")
	}
	shutdownCoordinator(t, coordinator)
}

func TestPostJoinLeaseLossSuppressesTerminalEvenIfJoinReturnsNil(t *testing.T) {
	fake := newFakeDependencies()
	fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
		return &lossAtJoinHandle{fakeHandle: newFakeHandle(grant.Fence)}, nil
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrLeaseLost) {
		t.Fatalf("got %v", result.Err)
	}
	_, _, _, terminals := fake.counts()
	if terminals != 0 {
		t.Fatal("terminal ran after post-join loss")
	}
	shutdownCoordinator(t, coordinator)
}

func TestExecutorPanicStillStopsAndJoinsWithoutTerminal(t *testing.T) {
	fake := newFakeDependencies()
	handleReady := make(chan *fakeHandle, 1)
	fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
		handle := newFakeHandle(grant.Fence)
		handleReady <- handle
		return handle, nil
	}
	fake.executeHook = func(context.Context, Candidate, Fence) (Execution, error) {
		panic("secret panic value")
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	result := awaitResult(t, coordinator)
	if !errors.Is(result.Err, ErrDependencyPanic) || strings.Contains(result.Err.Error(), "secret") {
		t.Fatalf("panic was not safely contained: %v", result.Err)
	}
	handle := awaitValue(t, handleReady, "panic handle")
	if handle.stops.Load() != 1 || handle.joins.Load() != 1 {
		t.Fatalf("cleanup counts: stop=%d join=%d", handle.stops.Load(), handle.joins.Load())
	}
	_, _, _, terminals := fake.counts()
	if terminals != 0 {
		t.Fatal("terminal ran after executor panic")
	}
	shutdownCoordinator(t, coordinator)
}

func TestClaimPanicReleasesCloseGateAndCapacity(t *testing.T) {
	fake := newFakeDependencies()
	fake.claimHook = func(context.Context, Candidate) (ClaimGrant, error) {
		panic("secret claim panic")
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrDependencyPanic) || strings.Contains(result.Err.Error(), "secret") {
		t.Fatalf("claim panic was not contained: %v", result.Err)
	}
	closed := make(chan struct{})
	go func() {
		coordinator.Close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("claim panic leaked the close gate")
	}
	for range coordinator.Results() {
	}
	awaitSignal(t, coordinator.Done(), "backpressured coordinator")
}

func TestSupervisorStartFailureAndPanicAreContained(t *testing.T) {
	startFailure := errors.New("start failed")
	tests := map[string]struct {
		hook        func(context.Context, ClaimGrant) (LeaseHandle, error)
		want        error
		wantCleanup bool
	}{
		"handle plus error": {
			hook: func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
				return newFakeHandle(grant.Fence), startFailure
			},
			want:        startFailure,
			wantCleanup: true,
		},
		"panic": {
			hook: func(context.Context, ClaimGrant) (LeaseHandle, error) {
				panic("secret start panic")
			},
			want: ErrDependencyPanic,
		},
	}
	for name, test := range tests {
		t.Run(name, func(t *testing.T) {
			fake := newFakeDependencies()
			var returned *fakeHandle
			fake.startHook = func(ctx context.Context, grant ClaimGrant) (LeaseHandle, error) {
				handle, err := test.hook(ctx, grant)
				if value, ok := handle.(*fakeHandle); ok {
					returned = value
				}
				return handle, err
			}
			coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
			candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
			if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
				t.Fatal(err)
			}
			result := awaitResult(t, coordinator)
			if !errors.Is(result.Err, test.want) || strings.Contains(result.Err.Error(), "secret") {
				t.Fatalf("got %v want %v", result.Err, test.want)
			}
			if test.wantCleanup && (returned == nil || returned.stops.Load() != 1 || returned.joins.Load() != 1) {
				t.Fatalf("start error handle not cleaned: %#v", returned)
			}
			_, _, executions, terminals := fake.counts()
			if executions != 0 || terminals != 0 {
				t.Fatalf("later phase ran: executions=%d terminals=%d", executions, terminals)
			}
			shutdownCoordinator(t, coordinator)
		})
	}
}

func TestHandleInspectionPanicCleansUpAndKeepsPanicClass(t *testing.T) {
	fake := newFakeDependencies()
	var underlying *fakeHandle
	fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
		underlying = newFakeHandle(grant.Fence)
		return &panicInspectHandle{fakeHandle: underlying}, nil
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrDependencyPanic) {
		t.Fatalf("got %v", result.Err)
	}
	if underlying == nil || underlying.stops.Load() != 1 || underlying.joins.Load() != 1 {
		t.Fatalf("inspection panic leaked handle: %#v", underlying)
	}
	shutdownCoordinator(t, coordinator)
}

func TestExecutorErrorRequiresExactFenceBeforeTerminal(t *testing.T) {
	executionFailure := errors.New("execution failed")
	for _, exact := range []bool{false, true} {
		t.Run(fmt.Sprintf("exact=%t", exact), func(t *testing.T) {
			fake := newFakeDependencies()
			fake.executeHook = func(_ context.Context, _ Candidate, fence Fence) (Execution, error) {
				if !exact {
					fence.ClaimToken += "changed"
				}
				return Execution{Fence: fence}, executionFailure
			}
			coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
			candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
			if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
				t.Fatal(err)
			}
			result := awaitResult(t, coordinator)
			_, _, _, terminals := fake.counts()
			if exact {
				if !errors.Is(result.Err, executionFailure) || terminals != 1 {
					t.Fatalf("exact failure got err=%v terminals=%d", result.Err, terminals)
				}
			} else if !errors.Is(result.Err, ErrFenceMismatch) || terminals != 0 {
				t.Fatalf("mismatch got err=%v terminals=%d", result.Err, terminals)
			}
			shutdownCoordinator(t, coordinator)
		})
	}
}

func TestTerminalCannotMutatePublishedResult(t *testing.T) {
	fake := newFakeDependencies()
	retained := make(chan Execution, 1)
	fake.executeHook = func(_ context.Context, _ Candidate, fence Fence) (Execution, error) {
		return Execution{Fence: fence, Result: sitemap.Result{URLs: []string{"https://original.example/job"}}}, nil
	}
	fake.terminalHook = func(_ context.Context, _ Candidate, execution Execution, _ error) (Fence, error) {
		execution.Result.URLs[0] = "https://mutated.example/job"
		retained <- execution
		return execution.Fence, nil
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	result := awaitResult(t, coordinator)
	if result.Err != nil || len(result.Sitemap.URLs) != 1 || result.Sitemap.URLs[0] != "https://original.example/job" {
		t.Fatalf("terminal mutated published result: %#v", result)
	}
	kept := awaitValue(t, retained, "retained terminal execution")
	kept.Result.URLs[0] = "https://retained.example/job"
	if result.Sitemap.URLs[0] != "https://original.example/job" {
		t.Fatal("terminal retained published result backing storage")
	}
	shutdownCoordinator(t, coordinator)
}

func TestTimeoutStopsAndJoinsWithoutTerminal(t *testing.T) {
	fake := newFakeDependencies()
	fake.executeHook = func(ctx context.Context, _ Candidate, fence Fence) (Execution, error) {
		<-ctx.Done()
		return Execution{Fence: fence}, ctx.Err()
	}
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, 20*time.Millisecond), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, context.DeadlineExceeded) {
		t.Fatalf("got %v", result.Err)
	}
	_, _, _, terminals := fake.counts()
	if terminals != 0 {
		t.Fatal("terminal ran after timeout")
	}
	shutdownCoordinator(t, coordinator)
}

func TestBoundedCandidateCarriageUnderResultBackpressure(t *testing.T) {
	const capacity = 2
	fake := newFakeDependencies()
	coordinator := newTestCoordinator(t, testConfig(1, capacity, 1, 1, time.Second), fake)

	// The first result fills the sole result slot. The second worker then blocks
	// publishing, retaining its worker slot while many callers contend.
	for index := range 2 {
		candidate := fixtureCandidate(t, fmt.Sprintf("seed-%d", index), fmt.Sprintf("seed-board-%d", index), fmt.Sprintf("https://seed%d.example/sitemap.xml", index))
		if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
			t.Fatal(err)
		}
	}
	deadline := time.Now().Add(time.Second)
	for coordinator.Stats().Completed < 1 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if coordinator.Stats().Completed < 1 {
		t.Fatal("first result never filled the backpressure slot")
	}

	const contenders = 24
	errorsOut := make(chan error, contenders)
	start := make(chan struct{})
	contenderCandidates := make([]Candidate, 0, contenders)
	for index := range contenders {
		contenderCandidates = append(contenderCandidates, fixtureCandidate(t, fmt.Sprintf("blocked-%d", index), fmt.Sprintf("blocked-board-%d", index), fmt.Sprintf("https://blocked%d.example/sitemap.xml", index)))
	}
	for index := range contenders {
		go func(index int) {
			<-start
			ctx, cancel := context.WithTimeout(context.Background(), 40*time.Millisecond)
			defer cancel()
			errorsOut <- coordinator.Submit(ctx, context.Background(), contenderCandidates[index])
		}(index)
	}
	close(start)
	until := time.Now().Add(60 * time.Millisecond)
	for time.Now().Before(until) {
		stats := coordinator.Stats()
		outstanding := stats.Accepted - stats.Completed
		if outstanding > uint64(capacity) || stats.Queued+stats.InFlight > int64(capacity) {
			t.Fatalf("worker bound exceeded: outstanding=%d queued=%d inflight=%d", outstanding, stats.Queued, stats.InFlight)
		}
		time.Sleep(time.Millisecond)
	}
	for range contenders {
		err := awaitValue(t, errorsOut, "contender result")
		if err != nil && !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("contender error: %v", err)
		}
	}
	coordinator.Close()
	for range coordinator.Results() {
	}
	awaitSignal(t, coordinator.Done(), "bounded coordinator")
}

func TestJoinTimeoutSuppressesTerminal(t *testing.T) {
	fake := newFakeDependencies()
	fake.startHook = func(_ context.Context, grant ClaimGrant) (LeaseHandle, error) {
		return &blockingJoinHandle{fakeHandle: newFakeHandle(grant.Fence)}, nil
	}
	config := testConfig(1, 1, 1, 1, time.Second)
	config.JoinTimeout = 10 * time.Millisecond
	coordinator := newTestCoordinator(t, config, fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, coordinator); !errors.Is(result.Err, ErrSupervisorJoin) {
		t.Fatalf("got %v", result.Err)
	}
	_, _, _, terminals := fake.counts()
	if terminals != 0 {
		t.Fatal("terminal ran after join timeout")
	}
	shutdownCoordinator(t, coordinator)
}

func TestCoordinatorRejectsNegativeWorkerCapacityWithoutPanic(t *testing.T) {
	fake := newFakeDependencies()
	config := testConfig(1, 1, 1, 1, time.Second)
	config.Worker.Capacity = -1
	if coordinator, err := New(config, fake, fake, fake, fake); coordinator != nil || !errors.Is(err, ErrInvalidConfig) {
		t.Fatalf("coordinator=%v err=%v", coordinator, err)
	}
}

func TestNilContextsAreRejectedByTheContextBoundary(t *testing.T) {
	fake := newFakeDependencies()
	coordinator := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), fake)
	candidate := fixtureCandidate(t, "task-1", "board-1", "https://one.example/sitemap.xml")
	if err := coordinator.Submit(nil, context.Background(), candidate); !errors.Is(err, ErrInvalidContext) {
		t.Fatalf("nil admission context got %v", err)
	}
	if err := coordinator.Submit(context.Background(), nil, candidate); !errors.Is(err, ErrInvalidContext) {
		t.Fatalf("nil execution context got %v", err)
	}
	if err := coordinator.Shutdown(nil); !errors.Is(err, ErrInvalidContext) {
		t.Fatalf("nil shutdown context got %v", err)
	}
	shutdownCoordinator(t, coordinator)
}

func TestCoordinatorsDoNotShareWorkerOrOriginPermits(t *testing.T) {
	firstFake := newFakeDependencies()
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	firstFake.executeHook = func(ctx context.Context, _ Candidate, fence Fence) (Execution, error) {
		close(firstStarted)
		select {
		case <-releaseFirst:
			return Execution{Fence: fence}, nil
		case <-ctx.Done():
			return Execution{Fence: fence}, ctx.Err()
		}
	}
	first := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), firstFake)
	secondFake := newFakeDependencies()
	second := newTestCoordinator(t, testConfig(1, 1, 1, 1, time.Second), secondFake)
	rawURL := "https://shared-origin.example/sitemap.xml"
	if err := first.Submit(context.Background(), context.Background(), fixtureCandidate(t, "first", "board-first", rawURL)); err != nil {
		t.Fatal(err)
	}
	awaitSignal(t, firstStarted, "first coordinator execution")
	if err := second.Submit(context.Background(), context.Background(), fixtureCandidate(t, "second", "board-second", rawURL)); err != nil {
		t.Fatal(err)
	}
	if result := awaitResult(t, second); result.Err != nil {
		t.Fatalf("second coordinator inherited first permit: %v", result.Err)
	}
	close(releaseFirst)
	if result := awaitResult(t, first); result.Err != nil {
		t.Fatal(result.Err)
	}
	shutdownCoordinator(t, first)
	shutdownCoordinator(t, second)
}

func TestHermeticHTTPCompositionPreservesDispatchAndConservation(t *testing.T) {
	const (
		workerCount = 3
		jobCount    = 4
	)
	release := make(chan struct{})
	started := make(chan int, jobCount)
	var globalActive atomic.Int64
	var globalMax atomic.Int64
	var requests atomic.Int64
	originActive := make([]atomic.Int64, 3)
	originMax := make([]atomic.Int64, 3)
	servers := make([]*httptest.Server, 0, 3)
	for index := range 3 {
		originIndex := index
		server := httptest.NewServer(http.HandlerFunc(func(response http.ResponseWriter, _ *http.Request) {
			requests.Add(1)
			active := globalActive.Add(1)
			storeAtomicMax(&globalMax, active)
			originNow := originActive[originIndex].Add(1)
			storeAtomicMax(&originMax[originIndex], originNow)
			started <- originIndex
			<-release
			originActive[originIndex].Add(-1)
			globalActive.Add(-1)
			_, _ = response.Write([]byte(fmt.Sprintf(`<urlset><url><loc>https://jobs.example/jobs/%d</loc></url></urlset>`, originIndex)))
		}))
		servers = append(servers, server)
		defer server.Close()
	}
	// Register this cleanup after the server cleanups so LIFO defer order
	// releases any blocked handlers before httptest.Server.Close waits for
	// their connections. This keeps an assertion failure bounded.
	defer func() {
		select {
		case <-release:
		default:
			close(release)
		}
	}()
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           time.Second,
		MaxDecodedBodyBytes:      1 << 20,
		MaxRequests:              3,
		MaxAggregateDecodedBytes: 2 << 20,
		AllowPrivateNetwork:      true,
		SharedTransport: &boundedhttp.SharedTransportConfig{
			MaxIdleConns:          workerCount,
			MaxIdleConnsPerHost:   1,
			MaxConnsPerHost:       workerCount,
			MaxConnections:        workerCount,
			MaxConcurrentRequests: workerCount,
			IdleConnTimeout:       time.Second,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()

	fake := newFakeDependencies()
	realExecutor := realSitemapExecutor{client: client}
	fake.executeHook = realExecutor.Execute
	coordinator := newTestCoordinator(t, testConfig(workerCount, jobCount, jobCount, 1, time.Second), fake)
	URLs := []string{servers[0].URL, servers[0].URL, servers[1].URL, servers[2].URL}
	for index, rawURL := range URLs {
		candidate := fixtureCandidate(t, fmt.Sprintf("task-%d", index), fmt.Sprintf("board-%d", index), rawURL+"/sitemap.xml")
		if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
			t.Fatal(err)
		}
	}
	firstOrigins := map[int]bool{}
	for range workerCount {
		firstOrigins[awaitValue(t, started, "hermetic origin start")] = true
	}
	if len(firstOrigins) != workerCount {
		t.Fatalf("first wave did not span origins: %v", firstOrigins)
	}
	assertNoInt(t, started, "duplicate origin HTTP request before release")
	if globalMax.Load() != workerCount {
		t.Fatalf("global HTTP overlap %d, want %d", globalMax.Load(), workerCount)
	}
	for index := range originMax {
		if originMax[index].Load() != 1 {
			t.Fatalf("origin %d overlap %d", index, originMax[index].Load())
		}
	}
	close(release)
	seen := map[string]bool{}
	for range jobCount {
		result := awaitResult(t, coordinator)
		if result.Err != nil || len(result.Sitemap.URLs) != 1 {
			t.Fatalf("result %s: err=%v urls=%v", result.JobID, result.Err, result.Sitemap.URLs)
		}
		seen[result.JobID] = true
	}
	shutdownCoordinator(t, coordinator)
	if len(seen) != jobCount || requests.Load() != jobCount {
		t.Fatalf("conservation: results=%d requests=%d", len(seen), requests.Load())
	}
	assertCounts(t, fake, jobCount, jobCount, jobCount, jobCount)
}

func TestAdversarialConcurrencyRemainsBoundedAndConserved(t *testing.T) {
	const (
		workerCount = 5
		jobCount    = 60
	)
	fake := newFakeDependencies()
	var active atomic.Int64
	var maxActive atomic.Int64
	activeByOrigin := map[string]int{}
	maxByOrigin := map[string]int{}
	var originMu sync.Mutex
	fake.executeHook = func(ctx context.Context, candidate Candidate, fence Fence) (Execution, error) {
		current := active.Add(1)
		storeAtomicMax(&maxActive, current)
		origin := strings.Split(candidate.Sitemap().SitemapURL, "/sitemap.xml")[0]
		originMu.Lock()
		activeByOrigin[origin]++
		if activeByOrigin[origin] > maxByOrigin[origin] {
			maxByOrigin[origin] = activeByOrigin[origin]
		}
		originMu.Unlock()
		select {
		case <-time.After(time.Duration(candidate.TaskID()[len(candidate.TaskID())-1]%3+1) * time.Millisecond):
		case <-ctx.Done():
		}
		originMu.Lock()
		activeByOrigin[origin]--
		originMu.Unlock()
		active.Add(-1)
		return Execution{Fence: fence}, ctx.Err()
	}
	coordinator := newTestCoordinator(t, testConfig(workerCount, jobCount, jobCount, 1, time.Second), fake)
	for index := range jobCount {
		origin := index % 6
		candidate := fixtureCandidate(t, fmt.Sprintf("task-%02d", index), fmt.Sprintf("board-%02d", index), fmt.Sprintf("https://o%d.example/sitemap.xml", origin))
		if err := coordinator.Submit(context.Background(), context.Background(), candidate); err != nil {
			t.Fatalf("Submit %d: %v", index, err)
		}
	}
	for range jobCount {
		if result := awaitResult(t, coordinator); result.Err != nil {
			t.Fatalf("result %s: %v", result.JobID, result.Err)
		}
	}
	shutdownCoordinator(t, coordinator)
	if got := maxActive.Load(); got != workerCount {
		t.Fatalf("max active %d, want %d", got, workerCount)
	}
	for origin, maximum := range maxByOrigin {
		if maximum != 1 {
			t.Fatalf("origin %s max %d", origin, maximum)
		}
	}
	assertCounts(t, fake, jobCount, jobCount, jobCount, jobCount)
}

func assertNoString(t *testing.T, values <-chan string, description string) {
	t.Helper()
	select {
	case value := <-values:
		t.Fatalf("%s: %s", description, value)
	case <-time.After(30 * time.Millisecond):
	}
}

func assertNoInt(t *testing.T, values <-chan int, description string) {
	t.Helper()
	select {
	case value := <-values:
		t.Fatalf("%s: %d", description, value)
	case <-time.After(30 * time.Millisecond):
	}
}

func awaitSignal(t *testing.T, signal <-chan struct{}, description string) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(2 * time.Second):
		t.Fatalf("timed out waiting for %s", description)
	}
}

func awaitString(t *testing.T, values <-chan string, description string) string {
	return awaitValue(t, values, description)
}

func awaitValue[T any](t *testing.T, values <-chan T, description string) T {
	t.Helper()
	select {
	case value := <-values:
		return value
	case <-time.After(2 * time.Second):
		t.Fatalf("timed out waiting for %s", description)
		var zero T
		return zero
	}
}

func assertCounts(t *testing.T, fake *fakeDependencies, claims, starts, executions, terminals int) {
	t.Helper()
	gotClaims, gotStarts, gotExecutions, gotTerminals := fake.counts()
	if gotClaims != claims || gotStarts != starts || gotExecutions != executions || gotTerminals != terminals {
		t.Fatalf("phase counts got %d/%d/%d/%d want %d/%d/%d/%d", gotClaims, gotStarts, gotExecutions, gotTerminals, claims, starts, executions, terminals)
	}
}

func storeAtomicMax(target *atomic.Int64, candidate int64) {
	for current := target.Load(); candidate > current; current = target.Load() {
		if target.CompareAndSwap(current, candidate) {
			return
		}
	}
}
