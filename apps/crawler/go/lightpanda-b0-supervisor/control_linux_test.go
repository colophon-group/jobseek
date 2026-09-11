//go:build linux

package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

func controlConnections(t *testing.T) (*net.UnixConn, *net.UnixConn) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "producer.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	client, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	server, err := listener.AcceptUnix()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	return client, server
}

func startControlHandler(t *testing.T, server *net.UnixConn, producer *b0Producer, acceptedUID uint32) *producerAuthorityLatch {
	t.Helper()
	authorityContext, cancel := context.WithCancel(context.Background())
	latch := newProducerAuthorityLatch(cancel)
	t.Cleanup(cancel)
	go handleProducerConnection(server, producer, acceptedUID, authorityContext, latch)
	return latch
}

func controlExchange(t *testing.T, path string, request producerRequest) producerResponse {
	return controlExchangeObserved(t, path, request, nil)
}

func controlExchangeObserved(t *testing.T, path string, request producerRequest, connected *atomic.Int32) producerResponse {
	t.Helper()
	connection, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	_ = connection.SetDeadline(time.Now().Add(producerTimeout))
	payload, err := canonicalJSON(request, true)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(payload, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connection.Write(record); err != nil {
		t.Fatal(err)
	}
	if connected != nil {
		connected.Add(1)
	}
	responsePayload, err := framing.ReadRecord(connection, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	var response producerResponse
	if err := json.Unmarshal(responsePayload, &response); err != nil {
		t.Fatal(err)
	}
	return response
}

func waitForProducerReady(t *testing.T, path string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())) == nil {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("producer control socket did not become ready")
}

func TestProducerControlAuthenticatesPeerAndReturnsOneStrictFrame(t *testing.T) {
	client, server := controlConnections(t)
	queue := &fakeProducerQueue{}
	startControlHandler(t, server, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
	request, err := canonicalJSON(validProducerRequest(), true)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(request, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	_ = client.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := client.Write(record); err != nil {
		t.Fatal(err)
	}
	payload, err := framing.ReadRecord(client, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := parseCanonicalValue(payload)
	if err != nil {
		t.Fatal(err)
	}
	reencoded, err := canonicalJSON(canonical, true)
	if err != nil || string(reencoded) != string(payload) {
		t.Fatal("control response was not canonical JSON")
	}
	var response producerResponse
	if err := json.Unmarshal(payload, &response); err != nil || response.Outcome != "prepared" || response.Reason != "prepared" {
		t.Fatalf("unexpected control response: %#v, %v", response, err)
	}
	buffer := make([]byte, 1)
	if count, err := client.Read(buffer); count != 0 || err == nil {
		t.Fatalf("control server did not close after one response: %d, %v", count, err)
	}
}

func TestProducerControlRejectsUnauthenticatedPeerWithoutResponse(t *testing.T) {
	client, server := controlConnections(t)
	queue := &fakeProducerQueue{}
	startControlHandler(t, server, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()+1))
	_ = client.SetReadDeadline(time.Now().Add(2 * time.Second))
	buffer := make([]byte, 1)
	if count, err := client.Read(buffer); count != 0 || err == nil {
		t.Fatalf("unauthenticated peer received control data: %d, %v", count, err)
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("unauthenticated peer reached producer mutation")
	}
}

func TestProducerControlRejectsNoncanonicalFraming(t *testing.T) {
	client, server := controlConnections(t)
	queue := &fakeProducerQueue{}
	startControlHandler(t, server, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
	_ = client.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := client.Write([]byte{0x80, 0x00}); err != nil {
		t.Fatal(err)
	}
	buffer := make([]byte, 1)
	if count, err := client.Read(buffer); count != 0 || err == nil {
		t.Fatalf("noncanonical frame received control data: %d, %v", count, err)
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("noncanonical frame reached producer mutation")
	}
}

func TestProducerSocketDirectoryRequiresPrivateOwnerMode(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := validateProducerSocketDirectory(directory, uint32(os.Geteuid())); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(directory, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := validateProducerSocketDirectory(directory, uint32(os.Geteuid())); err == nil {
		t.Fatal("public producer socket directory was accepted")
	}
}

func TestSecondProducerCannotUnlinkLiveFirstProducerSocket(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	queue := &fakeProducerQueue{}
	producer := validProducer(t, queue, "browser-use-careers")
	ctx, cancel := context.WithCancel(context.Background())
	firstErr := make(chan error, 1)
	go func() {
		firstErr <- serveProducerAt(ctx, path, producer, uint32(os.Geteuid()))
	}()
	waitForProducerReady(t, path)

	secondErr := serveProducerAt(context.Background(), path, producer, uint32(os.Geteuid()))
	if secondErr == nil || !strings.Contains(secondErr.Error(), "live producer socket") {
		t.Fatalf("second producer did not reject the live first socket: %v", secondErr)
	}
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err != nil {
		t.Fatalf("first producer became unreachable after second startup: %v", err)
	}

	cancel()
	select {
	case err := <-firstErr:
		if err != nil {
			t.Fatalf("first producer did not stop cleanly: %v", err)
		}
	case <-time.After(2 * producerTimeout):
		t.Fatal("first producer did not stop")
	}
}

func TestProducerControlMapsLostRedisAuthorityToClosedFailure(t *testing.T) {
	client, server := controlConnections(t)
	queue := &fakeProducerQueue{inspectErr: context.DeadlineExceeded}
	producer := validProducer(t, queue, "browser-use-careers")
	latch := startControlHandler(t, server, producer, uint32(os.Geteuid()))
	request, err := canonicalJSON(validProducerRequest(), true)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(request, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	_ = client.SetDeadline(time.Now().Add(2 * time.Second))
	if _, err := client.Write(record); err != nil {
		t.Fatal(err)
	}
	payload, err := framing.ReadRecord(client, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	var response producerResponse
	if err := json.Unmarshal(payload, &response); err != nil || response.Outcome != "error" || response.Reason != "authority_lost" {
		t.Fatalf("lost authority was not closed: %#v, %v", response, err)
	}
	if queue.initialized != 0 || queue.activated != 0 {
		t.Fatal("lost authority reached producer mutation")
	}
	if failure, ok := latch.failure(); !ok || failure.class != "redis" || failure.operation != "prepare" {
		t.Fatalf("lost authority was not latched: %#v, %v", failure, ok)
	}
}

func TestProducerServerBackpressuresFullDiscoveryBurstWithoutDropping(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	release := make(chan struct{})
	var current atomic.Int32
	var maximum atomic.Int32
	originalReadBoard := producer.readBoard
	producer.readBoard = func(ctx context.Context, boardID string) (map[string]string, error) {
		active := current.Add(1)
		defer current.Add(-1)
		for {
			observed := maximum.Load()
			if active <= observed || maximum.CompareAndSwap(observed, active) {
				break
			}
		}
		select {
		case <-release:
			return originalReadBoard(ctx, boardID)
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	serverErr := make(chan error, 1)
	go func() { serverErr <- serveProducerAt(ctx, path, producer, uint32(os.Geteuid())) }()
	waitForProducerReady(t, path)

	const clients = 67
	if producerBacklog < clients+5 {
		t.Fatalf("listen backlog lacks healthcheck headroom: %d", producerBacklog)
	}
	results := make(chan producerResponse, clients)
	var connected atomic.Int32
	var callers sync.WaitGroup
	callers.Add(clients)
	for range clients {
		go func() {
			defer callers.Done()
			results <- controlExchangeObserved(t, path, validProducerRequest(), &connected)
		}()
	}
	deadline := time.Now().Add(2 * time.Second)
	for (maximum.Load() < producerMaxHandlers || connected.Load() < clients) && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if maximum.Load() != producerMaxHandlers {
		t.Fatalf("server did not fill the bounded handler pool: %d", maximum.Load())
	}
	if connected.Load() != clients {
		t.Fatalf("bounded backlog did not admit the full discovery burst: %d/%d", connected.Load(), clients)
	}
	close(release)
	callers.Wait()
	close(results)
	for response := range results {
		if response.Outcome != "prepared" || response.Reason != "prepared" {
			t.Fatalf("backpressured client was dropped or refused: %#v", response)
		}
	}
	if maximum.Load() > producerMaxHandlers {
		t.Fatalf("handler bound exceeded: %d", maximum.Load())
	}
	cancel()
	if err := <-serverErr; err != nil {
		t.Fatalf("producer server did not stop cleanly: %v", err)
	}
}

func TestProducerAuthorityLossTerminatesServerAndDropsReadiness(t *testing.T) {
	for _, class := range []string{"redis", "fenced", "corruption"} {
		t.Run(class, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "producer.sock")
			queue := &fakeProducerQueue{inspectErr: queueAuthority(class, "inspect")}
			ctx, cancel := context.WithCancel(context.Background())
			t.Cleanup(cancel)
			serverErr := make(chan error, 1)
			go func() {
				serverErr <- serveProducerAt(ctx, path, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
			}()
			waitForProducerReady(t, path)
			response := controlExchange(t, path, validProducerRequest())
			if response.Outcome != "error" || response.Reason != "authority_lost" {
				t.Fatalf("authority failure was not closed: %#v", response)
			}
			select {
			case err := <-serverErr:
				var failure producerAuthorityLoss
				if !errors.As(err, &failure) || failure.class != class || failure.operation != "prepare" {
					t.Fatalf("server returned an unbounded or untyped failure: %#v", err)
				}
			case <-time.After(2 * producerTimeout):
				t.Fatal("authority-lost server did not stop within its bound")
			}
			if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
				t.Fatal("authority-lost server remained ready")
			}
		})
	}
}

func TestProducerCancellationWithActiveHandlerStopsCleanly(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	producer := validProducer(t, &fakeProducerQueue{}, "browser-use-careers")
	entered := make(chan struct{})
	producer.readBoard = func(ctx context.Context, _ string) (map[string]string, error) {
		close(entered)
		<-ctx.Done()
		return nil, ctx.Err()
	}
	ctx, cancel := context.WithCancel(context.Background())
	serverErr := make(chan error, 1)
	go func() { serverErr <- serveProducerAt(ctx, path, producer, uint32(os.Geteuid())) }()
	waitForProducerReady(t, path)
	connection, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer connection.Close()
	payload, err := canonicalJSON(validProducerRequest(), true)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(payload, producerFrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := connection.Write(record); err != nil {
		t.Fatal(err)
	}
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("producer handler did not enter its authority read")
	}
	cancel()
	select {
	case err := <-serverErr:
		if err != nil {
			t.Fatalf("ordinary cancellation was misclassified as authority loss: %v", err)
		}
	case <-time.After(2 * producerTimeout):
		t.Fatal("canceled producer server did not stop within its bound")
	}
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
		t.Fatal("canceled producer remained ready")
	}
}

func TestProducerStartupPreflightRejectsPersistentAuthorityBeforeReadiness(t *testing.T) {
	for _, class := range []string{"redis", "fenced", "corruption"} {
		t.Run(class, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "producer.sock")
			queue := &fakeProducerQueue{preflightFn: func(context.Context, bool) error {
				return queueAuthority(class, "preflight")
			}}
			err := serveProducerAt(context.Background(), path, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
			var failure producerAuthorityLoss
			if !errors.As(err, &failure) || failure.class != class || failure.operation != "preflight" {
				t.Fatalf("startup preflight returned the wrong failure: %#v", err)
			}
			if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("failed startup published readiness: %v", err)
			}
		})
	}
}

func TestProducerPeriodicFullAuditTearsDownReadiness(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	lost := make(chan struct{})
	var fullCalls atomic.Int32
	var shallowCalls atomic.Int32
	queue := &fakeProducerQueue{preflightFn: func(_ context.Context, full bool) error {
		if full {
			fullCalls.Add(1)
		} else {
			shallowCalls.Add(1)
		}
		select {
		case <-lost:
			return queueAuthority("fenced", "preflight")
		default:
			return nil
		}
	}}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	serverErr := make(chan error, 1)
	go func() {
		serverErr <- serveProducerAt(ctx, path, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
	}()
	waitForProducerReady(t, path)
	if fullCalls.Load() < 2 {
		t.Fatalf("startup and health did not perform full audits: %d", fullCalls.Load())
	}
	close(lost)
	select {
	case err := <-serverErr:
		var failure producerAuthorityLoss
		if !errors.As(err, &failure) || failure.class != "fenced" || failure.operation != "preflight" {
			t.Fatalf("periodic preflight returned the wrong failure: %#v", err)
		}
	case <-time.After(producerPreflightInterval + 2*producerTimeout):
		t.Fatal("periodic authority loss did not terminate the producer")
	}
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
		t.Fatal("periodic authority loss remained ready")
	}
	if fullCalls.Load() < 3 || shallowCalls.Load() != 0 {
		t.Fatalf("periodic/health did not stay on full audit: full=%d shallow=%d", fullCalls.Load(), shallowCalls.Load())
	}
}

func TestProducerHealthFullAuditLatchesCorruptionAfterReadiness(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	corrupt := atomic.Bool{}
	shallow := atomic.Bool{}
	queue := &fakeProducerQueue{preflightFn: func(_ context.Context, full bool) error {
		if !full {
			shallow.Store(true)
		}
		if corrupt.Load() {
			return queueAuthority("corruption", "audit")
		}
		return nil
	}}
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	serverErr := make(chan error, 1)
	go func() {
		serverErr <- serveProducerAt(ctx, path, validProducer(t, queue, "browser-use-careers"), uint32(os.Geteuid()))
	}()
	waitForProducerReady(t, path)
	corrupt.Store(true)
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
		t.Fatal("health accepted post-readiness queue corruption")
	}
	select {
	case err := <-serverErr:
		var failure producerAuthorityLoss
		if !errors.As(err, &failure) || failure.class != "corruption" || failure.operation != "health" {
			t.Fatalf("health corruption returned the wrong failure: %#v", err)
		}
	case <-time.After(producerTimeout):
		t.Fatal("health corruption did not terminate the producer")
	}
	if shallow.Load() {
		t.Fatal("health used a shallow authority probe")
	}
}

func TestProducerSIGTERMClosesIdleAndPartialFrameConnections(t *testing.T) {
	for _, partial := range []bool{false, true} {
		name := "idle"
		if partial {
			name = "partial-frame"
		}
		t.Run(name, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0o700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "producer.sock")
			ctx, cancel := context.WithCancel(context.Background())
			serverErr := make(chan error, 1)
			go func() {
				serverErr <- serveProducerAt(ctx, path, validProducer(t, &fakeProducerQueue{}, "browser-use-careers"), uint32(os.Geteuid()))
			}()
			waitForProducerReady(t, path)
			connection, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			defer connection.Close()
			_ = connection.SetDeadline(time.Now().Add(2 * producerTimeout))
			if partial {
				if _, err := connection.Write([]byte{0x81}); err != nil {
					t.Fatal(err)
				}
			}
			time.Sleep(100 * time.Millisecond)
			started := time.Now()
			cancel()
			select {
			case err := <-serverErr:
				if err != nil {
					t.Fatalf("SIGTERM-style cancellation failed: %v", err)
				}
			case <-time.After(time.Second):
				t.Fatal("idle connection delayed clean shutdown")
			}
			if time.Since(started) >= time.Second {
				t.Fatal("idle connection consumed the handler deadline")
			}
			buffer := make([]byte, 1)
			if count, err := connection.Read(buffer); count != 0 || err == nil {
				t.Fatalf("canceled connection remained open: %d, %v", count, err)
			}
		})
	}
}

func TestProducerReadinessAuthenticatesProtocolDirectoryAndClientIdentity(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	ctx, cancel := context.WithCancel(context.Background())
	serverErr := make(chan error, 1)
	go func() {
		serverErr <- serveProducerAt(ctx, path, validProducer(t, &fakeProducerQueue{}, "browser-use-careers"), uint32(os.Geteuid()))
	}()
	waitForProducerReady(t, path)
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid()+1)); err == nil {
		t.Fatal("readiness accepted the wrong configured mutation UID")
	}
	if err := os.Chmod(directory, 0o777); err != nil {
		t.Fatal(err)
	}
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
		t.Fatal("readiness accepted a public socket directory")
	}
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	cancel()
	if err := <-serverErr; err != nil {
		t.Fatal(err)
	}
}

func TestProducerReadinessRejectsSocketWithoutHealthProtocol(t *testing.T) {
	directory := t.TempDir()
	if err := os.Chmod(directory, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(directory, "producer.sock")
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		connection, acceptErr := listener.AcceptUnix()
		if acceptErr == nil {
			_ = connection.Close()
		}
	}()
	if err := checkProducerReady(path, uint32(os.Geteuid()), uint32(os.Geteuid())); err == nil {
		t.Fatal("readiness accepted a socket without the authenticated health protocol")
	}
	<-done
}
