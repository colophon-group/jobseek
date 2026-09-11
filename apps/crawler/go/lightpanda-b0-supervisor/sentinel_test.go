package main

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

func testProducerOwner() producerOwnerIdentity {
	return producerOwnerIdentity{
		Namespace: "production-b0", Cohort: "c1",
		Route:      routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner},
		BoardSlugs: []string{"browser-use-careers"},
	}
}

func TestProducerActivationSentinelIsDurableExactAndIdempotentlyCleared(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), testProducerOwner(), uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelAbsent {
		t.Fatalf("fresh sentinel was not absent: %v %v", state, err)
	}
	if err := sentinel.ensurePreparing(); err != nil {
		t.Fatal(err)
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelPreparing {
		t.Fatalf("persisted sentinel was not preparing: %v %v", state, err)
	}
	if err := sentinel.ensurePreparing(); err != nil {
		t.Fatalf("exact preparing sentinel was not idempotent: %v", err)
	}
	if err := sentinel.publishActive(); err != nil {
		t.Fatal(err)
	}
	if active, err := sentinel.isActive(); err != nil || !active {
		t.Fatalf("persisted sentinel was not active: %v %v", active, err)
	}
	if err := sentinel.publishActive(); err != nil {
		t.Fatalf("exact active sentinel was not idempotent: %v", err)
	}
	if err := sentinel.clear(); err != nil {
		t.Fatal(err)
	}
	if err := sentinel.clear(); err != nil {
		t.Fatalf("interrupted-after-clear retry was not idempotent: %v", err)
	}
}

func TestProducerActivationSentinelForcesModeUnderRestrictiveUmask(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), testProducerOwner(), uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	previous := syscall.Umask(0o777)
	ensureErr := sentinel.ensurePreparing()
	syscall.Umask(previous)
	if ensureErr != nil {
		t.Fatal(ensureErr)
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelPreparing {
		t.Fatalf("restrictive umask changed durable sentinel: %v %v", state, err)
	}
}

func TestRollbackCanClearSafePartialProducerSentinel(t *testing.T) {
	for _, payload := range [][]byte{{}, []byte("P\n{\"board_slugs\"")} {
		name := "zero"
		if len(payload) != 0 {
			name = "partial"
		}
		t.Run(name, func(t *testing.T) {
			directory := t.TempDir()
			path := filepath.Join(directory, ".activation-v1")
			if err := os.WriteFile(path, payload, 0o600); err != nil {
				t.Fatal(err)
			}
			if err := os.Chmod(path, 0o600); err != nil {
				t.Fatal(err)
			}
			sentinel, err := newProducerActivationSentinel(path, testProducerOwner(), uint32(os.Geteuid()))
			if err != nil {
				t.Fatal(err)
			}
			if err := sentinel.clearForRollback(); err != nil {
				t.Fatalf("safe %s marker was not rollback-recoverable: %v", name, err)
			}
			if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("safe %s marker survived rollback clear: %v", name, err)
			}
		})
	}
}

func TestRollbackRefusesUnsafeProducerSentinel(t *testing.T) {
	tests := map[string]func(string) error{
		"active-partial": func(path string) error {
			if err := os.WriteFile(path, []byte("A\n{\"board_slugs\""), 0o600); err != nil {
				return err
			}
			return os.Chmod(path, 0o600)
		},
		"wrong-content": func(path string) error {
			if err := os.WriteFile(path, []byte("not-a-marker"), 0o600); err != nil {
				return err
			}
			return os.Chmod(path, 0o600)
		},
		"wrong-mode": func(path string) error {
			return os.WriteFile(path, []byte("partial"), 0o644)
		},
		"hardlink": func(path string) error {
			if err := os.WriteFile(path, []byte("partial"), 0o600); err != nil {
				return err
			}
			if err := os.Chmod(path, 0o600); err != nil {
				return err
			}
			return os.Link(path, path+".link")
		},
		"oversize": func(path string) error {
			if err := os.WriteFile(path, make([]byte, producerSentinelMaxBytes+1), 0o600); err != nil {
				return err
			}
			return os.Chmod(path, 0o600)
		},
		"symlink": func(path string) error {
			target := path + ".target"
			if err := os.WriteFile(target, []byte("partial"), 0o600); err != nil {
				return err
			}
			return os.Symlink(target, path)
		},
	}
	for name, setup := range tests {
		t.Run(name, func(t *testing.T) {
			directory := t.TempDir()
			path := filepath.Join(directory, ".activation-v1")
			if err := setup(path); err != nil {
				t.Fatal(err)
			}
			sentinel, err := newProducerActivationSentinel(path, testProducerOwner(), uint32(os.Geteuid()))
			if err != nil {
				t.Fatal(err)
			}
			if err := sentinel.clearForRollback(); err == nil {
				t.Fatalf("unsafe %s sentinel was removed", name)
			}
			if _, err := os.Lstat(path); err != nil {
				t.Fatalf("unsafe %s sentinel did not remain: %v", name, err)
			}
		})
	}
}

type bootstrapRaceQueue struct {
	entered     chan struct{}
	release     chan struct{}
	initialized atomic.Bool
	persisted   atomic.Int64
}

func (q *bootstrapRaceQueue) preflight(context.Context, bool, producerOwnerIdentity) (bool, error) {
	return !q.initialized.Load(), nil
}

func (q *bootstrapRaceQueue) initializeProducer(ctx context.Context, _ producerOwnerIdentity) error {
	close(q.entered)
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-q.release:
		q.initialized.Store(true)
		return nil
	}
}

func (q *bootstrapRaceQueue) persistProducer(context.Context) error {
	q.persisted.Add(1)
	return nil
}
func (q *bootstrapRaceQueue) lifetimeOccupancy(context.Context) (int64, error) { return 0, nil }

func (q *bootstrapRaceQueue) inspect(context.Context, string) (*storedTask, error) {
	return nil, nil
}

func (q *bootstrapRaceQueue) activateLegacy(context.Context, *queueTask, int64, string, string, bool, bool, producerOwnerIdentity) (transition, error) {
	return transition{Decision: "accepted", Reason: "activated"}, nil
}

func TestProducerPreflightCannotObserveSentinelBeforeRedisInitialization(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	queue := &bootstrapRaceQueue{entered: make(chan struct{}), release: make(chan struct{})}
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	producer.queue = queue
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), producer.owner, uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	producer.sentinel = sentinel
	request := validProducerRequest()
	request.Operation, request.OperatorTransfer = "enqueue", false
	enqueueErr := make(chan error, 1)
	go func() {
		_, _, err := producer.enqueue(context.Background(), request)
		enqueueErr <- err
	}()
	select {
	case <-queue.entered:
	case <-time.After(time.Second):
		t.Fatal("producer did not enter Redis initialization")
	}
	if state, err := sentinel.state(); err != nil || state != producerSentinelPreparing {
		t.Fatalf("preparing sentinel was not durable before Redis mutation: %v %v", state, err)
	}
	preflightErr := make(chan error, 1)
	go func() { preflightErr <- producer.preflight(context.Background(), true) }()
	select {
	case err := <-preflightErr:
		t.Fatalf("preflight observed the sentinel/Redis transition window: %v", err)
	case <-time.After(25 * time.Millisecond):
	}
	close(queue.release)
	if err := <-enqueueErr; err != nil {
		t.Fatal(err)
	}
	if err := <-preflightErr; err != nil {
		t.Fatalf("preflight failed after atomic bootstrap section: %v", err)
	}
}

func TestProducerPreparingSentinelWithoutRedisInitializationRetriesBootstrap(t *testing.T) {
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), producer.owner, uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	if err := sentinel.ensurePreparing(); err != nil {
		t.Fatal(err)
	}
	producer.sentinel = sentinel
	queue := &bootstrapRaceQueue{entered: make(chan struct{}), release: make(chan struct{})}
	close(queue.release)
	producer.queue = queue
	release, err := producer.acquireMutationAuthority(context.Background())
	if err != nil {
		t.Fatalf("preparing bootstrap was not retryable: %v", err)
	}
	release()
	if state, stateErr := sentinel.state(); stateErr != nil || state != producerSentinelActive || queue.persisted.Load() != 1 {
		t.Fatalf("retry did not durably publish active: state=%v persisted=%d err=%v", state, queue.persisted.Load(), stateErr)
	}
}

func TestProducerPreparingSentinelWithInitializedRedisResavesBeforeActive(t *testing.T) {
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	sentinel, err := newProducerActivationSentinel(
		filepath.Join(directory, ".activation-v1"), producer.owner, uint32(os.Geteuid()),
	)
	if err != nil {
		t.Fatal(err)
	}
	if err := sentinel.ensurePreparing(); err != nil {
		t.Fatal(err)
	}
	producer.sentinel = sentinel
	initialized := &bootstrapRaceQueue{}
	initialized.initialized.Store(true)
	producer.queue = initialized
	release, err := producer.acquireMutationAuthority(context.Background())
	if err != nil {
		t.Fatalf("initialized preparing state was not recoverable: %v", err)
	}
	release()
	if state, stateErr := sentinel.state(); stateErr != nil || state != producerSentinelActive || initialized.persisted.Load() != 1 {
		t.Fatalf("recovery did not save before active: state=%v persisted=%d err=%v", state, initialized.persisted.Load(), stateErr)
	}
}

func TestSentinelResetRemovesOnlyExactStaleProducerSocket(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "lp-sock-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "control.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	listener.SetUnlinkOnClose(false)
	if err := os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := removeStaleProducerSocket(ctx, path, uint32(os.Geteuid())); err != nil {
		t.Fatalf("exact SIGKILL-stale socket was not recoverable: %v", err)
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("stale producer socket survived reset: %v", err)
	}
	if err := removeStaleProducerSocket(ctx, path, uint32(os.Geteuid())); err != nil {
		t.Fatalf("absent stale socket retry was not idempotent: %v", err)
	}
}

func TestSentinelResetRejectsLiveAndUnsafeProducerSockets(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "lp-sock-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	livePath := filepath.Join(directory, "live.sock")
	live, err := net.ListenUnix("unix", &net.UnixAddr{Name: livePath, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer live.Close()
	if err := os.Chmod(livePath, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := removeStaleProducerSocket(ctx, livePath, uint32(os.Geteuid())); err == nil ||
		!strings.Contains(err.Error(), "live producer socket") {
		t.Fatalf("live producer socket was accepted: %v", err)
	}
	if _, err := os.Lstat(livePath); err != nil {
		t.Fatalf("live producer socket was removed: %v", err)
	}

	unsafePath := filepath.Join(directory, "unsafe.sock")
	if err := os.WriteFile(unsafePath, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := removeStaleProducerSocket(ctx, unsafePath, uint32(os.Geteuid())); err == nil ||
		!strings.Contains(err.Error(), "unsafe producer socket") {
		t.Fatalf("unsafe producer path was accepted: %v", err)
	}
	if _, err := os.Lstat(unsafePath); err != nil {
		t.Fatalf("unsafe producer path was removed: %v", err)
	}
}
