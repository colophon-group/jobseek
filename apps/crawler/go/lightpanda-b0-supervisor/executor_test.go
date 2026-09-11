package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testExecutorConfig(socket string) config {
	return config{ExecutorSocket: socket, ExecutorTimeout: time.Second}
}

func shortSocketPath(t *testing.T) string {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "jobseek-b0-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(directory) })
	return filepath.Join(directory, "executor.sock")
}

func TestPythonExecutorHandshakeKeepsAuthorizationConversationBounded(t *testing.T) {
	current := validQueueTask(t)
	lease := &lease{Task: current, ClaimToken: "7:9", LeaseUntilMS: 20_000}
	socket := shortSocketPath(t)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	serverDone := make(chan error, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr != nil {
			serverDone <- acceptErr
			return
		}
		defer connection.Close()
		payload, readErr := readFramedJSON(connection)
		if readErr != nil {
			serverDone <- readErr
			return
		}
		var request executorRequest
		if err := json.Unmarshal(payload, &request); err != nil || request.ClaimToken != lease.ClaimToken || request.PayloadSHA256 != current.PayloadSHA256 {
			serverDone <- errors.New("supervisor request identity mismatch")
			return
		}
		if err := writeExecutorJSON(connection, executorMessage{Type: "authorize", ClaimToken: lease.ClaimToken, LeaseUntilMS: lease.LeaseUntilMS}); err != nil {
			serverDone <- err
			return
		}
		authorized, err := readExecutorMessageForTest(connection)
		if err != nil || authorized.Type != "authorized" || authorized.LeaseUntilMS != 80_000 {
			serverDone <- errors.New("supervisor authorization mismatch")
			return
		}
		next := int64(123_456)
		serverDone <- writeExecutorJSON(connection, executorMessage{Type: "committed", ClaimToken: lease.ClaimToken, LeaseUntilMS: authorized.LeaseUntilMS, NextReadyAtMS: &next})
	}()
	authorizeCalls := 0
	request := (&leaseAuthority{lease: lease}).executorRequest([]byte{1, 2, 3})
	next, err := runPythonExecutor(context.Background(), testExecutorConfig(socket), request, func(ctx context.Context, conversation commitConversation) (*int64, error) {
		authorizeCalls++
		return conversation(ctx, 80_000)
	})
	if err != nil || next == nil || *next != 123_456 || authorizeCalls != 1 {
		t.Fatalf("executor conversation failed: next=%v calls=%d err=%v", next, authorizeCalls, err)
	}
	if err := <-serverDone; err != nil {
		t.Fatal(err)
	}
}

func TestPythonExecutorCancellationActivelyClosesSocket(t *testing.T) {
	current := validQueueTask(t)
	lease := &lease{Task: current, ClaimToken: "7:10", LeaseUntilMS: 20_000}
	socket := shortSocketPath(t)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		t.Fatal(err)
	}
	accepted := make(chan net.Conn, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr == nil {
			accepted <- connection
		}
	}()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		request := (&leaseAuthority{lease: lease}).executorRequest([]byte{1})
		_, runErr := runPythonExecutor(ctx, testExecutorConfig(socket), request, func(context.Context, commitConversation) (*int64, error) {
			return nil, errors.New("must not authorize")
		})
		done <- runErr
	}()
	connection := <-accepted
	cancel()
	select {
	case runErr := <-done:
		if runErr == nil {
			t.Fatal("cancelled executor call succeeded")
		}
	case <-time.After(time.Second):
		t.Fatal("cancellation did not close the active executor socket")
	}
	_ = connection.Close()
	_ = listener.Close()
}

func TestPythonExecutorCommitTimeoutInterruptsStalledPeer(t *testing.T) {
	current := validQueueTask(t)
	lease := &lease{Task: current, ClaimToken: "7:11", LeaseUntilMS: 20_000}
	socket := shortSocketPath(t)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	serverDone := make(chan error, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr != nil {
			serverDone <- acceptErr
			return
		}
		defer connection.Close()
		if _, readErr := readFramedJSON(connection); readErr != nil {
			serverDone <- readErr
			return
		}
		if writeErr := writeExecutorJSON(connection, executorMessage{Type: "authorize", ClaimToken: lease.ClaimToken, LeaseUntilMS: lease.LeaseUntilMS}); writeErr != nil {
			serverDone <- writeErr
			return
		}
		authorized, readErr := readExecutorMessageForTest(connection)
		if readErr != nil || authorized.Type != "authorized" || authorized.LeaseUntilMS != 80_000 {
			serverDone <- errors.New("supervisor authorization mismatch")
			return
		}
		one := make([]byte, 1)
		_ = connection.SetReadDeadline(time.Now().Add(time.Second))
		if _, readErr = connection.Read(one); readErr == nil {
			serverDone <- errors.New("stalled executor connection remained open")
			return
		}
		serverDone <- nil
	}()
	request := (&leaseAuthority{lease: lease}).executorRequest([]byte{1})
	started := time.Now()
	_, err = runPythonExecutor(context.Background(), testExecutorConfig(socket), request, func(ctx context.Context, conversation commitConversation) (*int64, error) {
		commitContext, cancel := context.WithTimeout(ctx, 50*time.Millisecond)
		defer cancel()
		return conversation(commitContext, 80_000)
	})
	if err == nil {
		t.Fatal("stalled post-authorization executor was accepted")
	}
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("commit timeout did not bound socket I/O: %s", elapsed)
	}
	if err := <-serverDone; err != nil {
		t.Fatal(err)
	}
}

func TestPythonExecutorMapsPostgresFenceRejectionToAuthorityFailure(t *testing.T) {
	current := validQueueTask(t)
	lease := &lease{Task: current, ClaimToken: "7:14", LeaseUntilMS: 20_000}
	socket := shortSocketPath(t)
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	serverDone := make(chan error, 1)
	go func() {
		connection, acceptErr := listener.Accept()
		if acceptErr != nil {
			serverDone <- acceptErr
			return
		}
		defer connection.Close()
		if _, readErr := readFramedJSON(connection); readErr != nil {
			serverDone <- readErr
			return
		}
		if writeErr := writeExecutorJSON(connection, executorMessage{Type: "authorize", ClaimToken: lease.ClaimToken, LeaseUntilMS: lease.LeaseUntilMS}); writeErr != nil {
			serverDone <- writeErr
			return
		}
		authorized, readErr := readExecutorMessageForTest(connection)
		if readErr != nil || authorized.Type != "authorized" {
			serverDone <- errors.New("supervisor authorization mismatch")
			return
		}
		serverDone <- writeExecutorJSON(connection, executorMessage{Type: "authority_lost", Error: "postgres_write_fence_rejected"})
	}()
	request := (&leaseAuthority{lease: lease}).executorRequest([]byte{1})
	_, err = runPythonExecutor(context.Background(), testExecutorConfig(socket), request, func(ctx context.Context, conversation commitConversation) (*int64, error) {
		return conversation(ctx, 80_000)
	})
	var authorityFailure *authorityError
	if !errors.As(err, &authorityFailure) || authorityFailure.operation != "postgres-write-fence" {
		t.Fatalf("PostgreSQL fence rejection was not authority-fatal: %v", err)
	}
	if err := <-serverDone; err != nil {
		t.Fatal(err)
	}
}

func TestExecutorSocketMustBePrivateOwnedSocket(t *testing.T) {
	socket := shortSocketPath(t)
	if err := os.WriteFile(socket, []byte("not a socket"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := validateExecutorSocket(socket); err == nil {
		t.Fatal("regular file was accepted as the executor socket")
	}
	if err := os.Remove(socket); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	if err := os.Chmod(socket, 0o666); err != nil {
		t.Fatal(err)
	}
	if _, err := validateExecutorSocket(socket); err == nil {
		t.Fatal("non-private executor socket was accepted")
	}
	if err := os.Chmod(socket, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := validateExecutorSocket(socket); err != nil {
		t.Fatalf("private owned executor socket was rejected: %v", err)
	}
}

func TestExecutorResponseRejectsUnknownMissingAndNullFields(t *testing.T) {
	for _, payload := range []string{
		`{"type":"authorize","claim_token":"7:1","lease_until_ms":2,"extra":1}`,
		`{"type":"authorize","claim_token":"7:1"}`,
		`{"type":"committed","claim_token":"7:1","lease_until_ms":2,"next_ready_at_ms":null}`,
		`{"type":"error","error":"internal details"}`,
		`{"type":"authority_lost","error":"executor_failed"}`,
	} {
		framed, err := frameJSONForTest([]byte(payload))
		if err != nil {
			t.Fatal(err)
		}
		if _, err := readExecutorMessage(framed); err == nil {
			t.Fatalf("invalid response accepted: %s", payload)
		}
	}
}
