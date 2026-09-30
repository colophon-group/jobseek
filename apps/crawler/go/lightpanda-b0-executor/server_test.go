package executor

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

type testAttestor struct {
	stale atomic.Bool
	calls atomic.Int32
}

func (s *testAttestor) AttestEpoch(ctx context.Context, epoch int64) error {
	s.calls.Add(1)
	if s.stale.Load() || epoch != 7 {
		return ErrProtocol
	}
	return ctx.Err()
}

func startTestServer(t *testing.T, store EpochAttestor, handler TaskHandler, peer func(*net.UnixConn) (uint32, error)) (string, context.CancelFunc, <-chan error) {
	t.Helper()
	// Darwin's Unix socket path limit is shorter than Go's default test paths.
	dir, err := os.MkdirTemp("/tmp", "jobseek-b0-executor-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(dir) })
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "executor.sock")
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() {
		result <- (Server{Shard: "lightpanda-b0", Epoch: 7, Store: store, Task: handler}).serve(ctx, path, peer)
	}()
	deadline := time.Now().Add(3 * time.Second)
	for {
		if privateSocket(path) == nil {
			break
		}
		select {
		case err := <-result:
			cancel()
			t.Fatalf("startup: %v", err)
		default:
		}
		if time.Now().After(deadline) {
			cancel()
			t.Fatal("socket startup timeout")
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Cleanup(func() { cancel() })
	return path, cancel, result
}

func sameTestPeer(*net.UnixConn) (uint32, error) { return uint32(os.Geteuid()), nil }

func unixConversation(t *testing.T, path string) *net.UnixConn {
	t.Helper()
	conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = conn.Close() })
	_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
	return conn
}

func sendTaskFixture(t *testing.T, conn *net.UnixConn) {
	t.Helper()
	payload := protocolFixture(t)["request"]
	record, err := framing.EncodeRecord(payload, FrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.Copy(conn, bytes.NewReader(record)); err != nil {
		t.Fatal(err)
	}
}

func readKind(t *testing.T, conn *net.UnixConn) string {
	t.Helper()
	payload, err := ReadFrame(conn)
	if err != nil {
		t.Fatal(err)
	}
	var response struct{ Type string }
	if err := json.Unmarshal(payload, &response); err != nil {
		t.Fatal(err)
	}
	return response.Type
}

func TestHealthSlotRemainsAvailableWithFourTasks(t *testing.T) {
	store := &testAttestor{}
	started := make(chan struct{}, TaskCapacity)
	release := make(chan struct{})
	var handled atomic.Int32
	handler := func(ctx context.Context, conn *net.UnixConn, request Request) error {
		handled.Add(1)
		started <- struct{}{}
		select {
		case <-release:
		case <-ctx.Done():
			return ctx.Err()
		}
		return WriteMessage(conn, map[string]any{"type": "committed", "claim_token": request.ClaimToken, "lease_until_ms": request.LeaseUntilMS + 1})
	}
	path, cancel, done := startTestServer(t, store, handler, sameTestPeer)
	for range TaskCapacity {
		conn := unixConversation(t, path)
		sendTaskFixture(t, conn)
		select {
		case <-started:
		case <-time.After(3 * time.Second):
			t.Fatal("task not admitted")
		}
	}
	fifth := unixConversation(t, path)
	sendTaskFixture(t, fifth)
	if _, err := ReadFrame(fifth); err == nil {
		t.Fatal("fifth task admitted")
	}
	health := unixConversation(t, path)
	if err := WriteMessage(health, map[string]any{"version": Protocol, "type": "attest_route", "shard_id": "lightpanda-b0", "routing_epoch": 7}); err != nil {
		t.Fatal(err)
	}
	if readKind(t, health) != "route_attested" {
		t.Fatal("health rejected under load")
	}
	if handled.Load() != TaskCapacity || store.calls.Load() != 2 {
		t.Fatal("admission or attestation count mismatch")
	}
	close(release)
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown failed")
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("owned socket not removed")
	}
}

func TestPartialFirstFrameAndWrongPeerNeverConsumeTaskCapacity(t *testing.T) {
	for _, rejectPeer := range []bool{false, true} {
		t.Run(map[bool]string{false: "partial", true: "peer"}[rejectPeer], func(t *testing.T) {
			var handled atomic.Int32
			peer := sameTestPeer
			if rejectPeer {
				peer = func(*net.UnixConn) (uint32, error) { return uint32(os.Geteuid()) + 1, nil }
			}
			path, cancel, done := startTestServer(t, &testAttestor{}, func(context.Context, *net.UnixConn, Request) error { handled.Add(1); return nil }, peer)
			conn := unixConversation(t, path)
			_, _ = conn.Write([]byte{0x80})
			if rejectPeer {
				if _, err := ReadFrame(conn); err == nil {
					t.Fatal("wrong peer got a response")
				}
			} else if readKind(t, conn) != "error" {
				t.Fatal("partial frame not rejected")
			}
			if handled.Load() != 0 {
				t.Fatal("partial or wrong-peer task executed")
			}
			cancel()
			select {
			case err := <-done:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("shutdown failed")
			}
		})
	}
}

func TestPrivateSocketMetadataAndStaleEpochFailClosed(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "executor.sock")
	if err := os.Chmod(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if privateSocketDirectory(path) == nil {
		t.Fatal("public directory accepted")
	}
	if err := os.Chmod(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("preserve"), 0o600); err != nil {
		t.Fatal(err)
	}
	s := Server{Epoch: 7, Shard: "lightpanda-b0", Store: &testAttestor{}, Task: func(context.Context, *net.UnixConn, Request) error { return nil }}
	if err := s.serve(context.Background(), path, sameTestPeer); err == nil {
		t.Fatal("regular file replaced")
	}
	if body, _ := os.ReadFile(path); string(body) != "preserve" {
		t.Fatal("existing file damaged")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	store := &testAttestor{}
	store.stale.Store(true)
	s.Store = store
	if err := s.serve(context.Background(), path, sameTestPeer); err == nil {
		t.Fatal("stale epoch served")
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("stale epoch created socket")
	}
}

func TestTaskAuthorityLossIsSanitized(t *testing.T) {
	path, cancel, done := startTestServer(t, &testAttestor{}, func(context.Context, *net.UnixConn, Request) error { return ErrAuthorityLost }, sameTestPeer)
	conn := unixConversation(t, path)
	sendTaskFixture(t, conn)
	payload, err := ReadFrame(conn)
	if err != nil {
		t.Fatal(err)
	}
	if string(payload) != `{"error":"postgres_write_fence_rejected","type":"authority_lost"}` {
		t.Fatalf("unsanitized response: %s", payload)
	}
	cancel()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown failed")
	}
}

func TestShutdownLetsAuthorizedConversationFinish(t *testing.T) {
	started := make(chan struct{})
	release := make(chan struct{})
	path, cancel, done := startTestServer(t, &testAttestor{}, func(ctx context.Context, conn *net.UnixConn, request Request) error {
		close(started)
		select {
		case <-release:
		case <-ctx.Done():
			return ctx.Err()
		}
		return WriteMessage(conn, map[string]any{"type": "committed", "claim_token": request.ClaimToken, "lease_until_ms": request.LeaseUntilMS + 1})
	}, sameTestPeer)
	conn := unixConversation(t, path)
	sendTaskFixture(t, conn)
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("task not admitted")
	}
	cancel()
	select {
	case err := <-done:
		t.Fatalf("inflight conversation abandoned: %v", err)
	case <-time.After(30 * time.Millisecond):
	}
	close(release)
	if readKind(t, conn) != "committed" {
		t.Fatal("commit not acknowledged during grace")
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("shutdown failed")
	}
}

func TestShutdownCancelsAtCommitGrace(t *testing.T) {
	started := make(chan struct{})
	finished := make(chan struct{})
	path, cancel, done := startTestServer(t, &testAttestor{}, func(ctx context.Context, _ *net.UnixConn, _ Request) error {
		close(started)
		<-ctx.Done()
		close(finished)
		return ctx.Err()
	}, sameTestPeer)
	conn := unixConversation(t, path)
	sendTaskFixture(t, conn)
	select {
	case <-started:
	case <-time.After(3 * time.Second):
		t.Fatal("task not admitted")
	}
	before := time.Now()
	cancel()
	select {
	case err := <-done:
		if err == nil || time.Since(before) < CommitTimeout-100*time.Millisecond {
			t.Fatalf("shutdown grace not enforced: %v", err)
		}
	case <-time.After(CommitTimeout + 3*time.Second):
		t.Fatal("shutdown was unbounded")
	}
	select {
	case <-finished:
	case <-time.After(time.Second):
		t.Fatal("conversation context not cancelled")
	}
}
