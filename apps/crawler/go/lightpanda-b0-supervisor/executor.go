package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

const executorFrameLimit = uint64(3 * 1024 * 1024)

var errExecutorAuthorityLost = errors.New("Python executor lost PostgreSQL write authority")

type executorRequest struct {
	Version       string `json:"version"`
	TaskPayload   string `json:"task_payload"`
	PayloadSHA256 string `json:"payload_sha256"`
	ClaimToken    string `json:"claim_token"`
	LeaseUntilMS  int64  `json:"lease_until_ms"`
	BrowserResult []byte `json:"browser_result"`
}

type executorMessage struct {
	Type          string `json:"type"`
	ClaimToken    string `json:"claim_token,omitempty"`
	LeaseUntilMS  int64  `json:"lease_until_ms,omitempty"`
	NextReadyAtMS *int64 `json:"next_ready_at_ms,omitempty"`
	Error         string `json:"error,omitempty"`
}

type commitConversation func(context.Context, int64) (*int64, error)
type authorizeCommit func(context.Context, commitConversation) (*int64, error)

func runPythonExecutor(ctx context.Context, c config, request executorRequest, authorize authorizeCommit) (*int64, error) {
	if request.Version != "jobseek.lightpanda.executor/v1" || request.TaskPayload == "" || !hex256.MatchString(request.PayloadSHA256) ||
		request.ClaimToken == "" || request.LeaseUntilMS < 1 || len(request.BrowserResult) == 0 || len(request.BrowserResult) > 2*1024*1024 || authorize == nil {
		return nil, errors.New("invalid Python executor invocation")
	}
	before, err := validateExecutorSocket(c.ExecutorSocket)
	if err != nil {
		return nil, fmt.Errorf("validate DB-only Python executor socket: %w", err)
	}
	dialer := net.Dialer{Timeout: 3 * time.Second}
	connection, err := dialer.DialContext(ctx, "unix", c.ExecutorSocket)
	if err != nil {
		return nil, fmt.Errorf("connect DB-only Python executor: %w", err)
	}
	defer connection.Close()
	after, err := validateExecutorSocket(c.ExecutorSocket)
	if err != nil || before != after {
		return nil, errors.New("DB-only Python executor socket identity changed during connect")
	}
	stopInterrupt := make(chan struct{})
	go func() {
		select {
		case <-ctx.Done():
			_ = connection.Close()
		case <-stopInterrupt:
		}
	}()
	defer close(stopInterrupt)
	if deadline, ok := ctx.Deadline(); ok {
		_ = connection.SetDeadline(deadline)
	}
	if err := writeExecutorJSON(connection, request); err != nil {
		return nil, err
	}
	first, err := readExecutorMessage(connection)
	if err != nil {
		return nil, err
	}
	if first.Type == "authority_lost" {
		return nil, &authorityError{operation: "postgres-write-fence", err: errExecutorAuthorityLost}
	}
	if first.Type == "error" {
		return nil, errors.New("Python executor rejected the rendered task")
	}
	if first.Type != "authorize" || first.ClaimToken != request.ClaimToken || first.LeaseUntilMS != request.LeaseUntilMS || first.NextReadyAtMS != nil || first.Error != "" {
		return nil, errors.New("Python executor sent an invalid authorization request")
	}
	nextReady, err := authorize(ctx, func(commitContext context.Context, authorizedLeaseUntil int64) (*int64, error) {
		if authorizedLeaseUntil <= first.LeaseUntilMS {
			return nil, errors.New("authorization did not advance lease")
		}
		if deadline, ok := commitContext.Deadline(); ok {
			if err := connection.SetDeadline(deadline); err != nil {
				return nil, fmt.Errorf("bound Python executor commit socket: %w", err)
			}
		}
		stopCommitInterrupt := context.AfterFunc(commitContext, func() { _ = connection.Close() })
		defer stopCommitInterrupt()
		if err := commitContext.Err(); err != nil {
			return nil, err
		}
		if err := writeExecutorJSON(connection, executorMessage{Type: "authorized", ClaimToken: request.ClaimToken, LeaseUntilMS: authorizedLeaseUntil}); err != nil {
			return nil, err
		}
		committed, readErr := readExecutorMessage(connection)
		if readErr != nil {
			return nil, readErr
		}
		if committed.Type == "authority_lost" {
			return nil, errExecutorAuthorityLost
		}
		if committed.Type != "committed" || committed.ClaimToken != request.ClaimToken || committed.LeaseUntilMS != authorizedLeaseUntil || committed.Error != "" {
			return nil, errors.New("Python executor sent an invalid commit acknowledgement")
		}
		select {
		case <-commitContext.Done():
			return nil, commitContext.Err()
		default:
		}
		return committed.NextReadyAtMS, nil
	})
	if err != nil {
		if errors.Is(err, errExecutorAuthorityLost) {
			return nil, &authorityError{operation: "postgres-write-fence", err: err}
		}
		return nil, err
	}
	return nextReady, nil
}

type executorSocketIdentity struct {
	device uint64
	inode  uint64
}

func validateExecutorSocket(path string) (executorSocketIdentity, error) {
	if path != "/run/jobseek-lightpanda-executor/executor.sock" && !strings.HasSuffix(path, "/executor.sock") {
		return executorSocketIdentity{}, errors.New("unexpected executor socket basename")
	}
	parent, err := os.Lstat(filepath.Dir(path))
	if err != nil || !parent.IsDir() || parent.Mode().Perm() != 0o700 {
		return executorSocketIdentity{}, errors.New("executor socket directory is not private")
	}
	parentStat, ok := parent.Sys().(*syscall.Stat_t)
	if !ok || parentStat.Uid != uint32(os.Geteuid()) {
		return executorSocketIdentity{}, errors.New("executor socket directory owner is invalid")
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0o600 {
		return executorSocketIdentity{}, errors.New("executor socket metadata is invalid")
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return executorSocketIdentity{}, errors.New("executor socket owner is invalid")
	}
	return executorSocketIdentity{device: uint64(stat.Dev), inode: uint64(stat.Ino)}, nil
}

func writeExecutorJSON(output io.Writer, value any) error {
	payload, err := json.Marshal(value)
	if err != nil {
		return err
	}
	record, err := framing.EncodeRecord(payload, executorFrameLimit)
	if err != nil {
		return err
	}
	for len(record) != 0 {
		written, writeErr := output.Write(record)
		if writeErr != nil {
			return writeErr
		}
		if written <= 0 {
			return io.ErrShortWrite
		}
		record = record[written:]
	}
	return nil
}

func readExecutorMessage(input io.Reader) (executorMessage, error) {
	payload, err := framing.ReadRecord(input, executorFrameLimit)
	if err != nil {
		return executorMessage{}, fmt.Errorf("read Python executor frame: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	var fields map[string]json.RawMessage
	if err := decoder.Decode(&fields); err != nil || fields == nil {
		return executorMessage{}, errors.New("Python executor response is invalid JSON")
	}
	if err := decoder.Decode(&struct{}{}); !errors.Is(err, io.EOF) {
		return executorMessage{}, errors.New("Python executor response has trailing JSON")
	}
	var messageType string
	if err := json.Unmarshal(fields["type"], &messageType); err != nil {
		return executorMessage{}, errors.New("Python executor response has invalid type")
	}
	required := map[string]struct{}{"type": {}}
	switch messageType {
	case "authorize":
		required["claim_token"], required["lease_until_ms"] = struct{}{}, struct{}{}
	case "committed":
		required["claim_token"], required["lease_until_ms"] = struct{}{}, struct{}{}
		if raw, ok := fields["next_ready_at_ms"]; ok {
			if string(raw) == "null" {
				return executorMessage{}, errors.New("Python executor response has null ready time")
			}
			required["next_ready_at_ms"] = struct{}{}
		}
	case "error":
		required["error"] = struct{}{}
	case "authority_lost":
		required["error"] = struct{}{}
	default:
		return executorMessage{}, errors.New("Python executor response has unknown type")
	}
	if len(fields) != len(required) {
		return executorMessage{}, errors.New("Python executor response fields are invalid")
	}
	for name := range fields {
		if _, ok := required[name]; !ok {
			return executorMessage{}, errors.New("Python executor response fields are invalid")
		}
	}
	decoder = json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	var message executorMessage
	if err := decoder.Decode(&message); err != nil {
		return executorMessage{}, errors.New("Python executor response is invalid JSON")
	}
	if message.Type == "error" || message.Type == "authority_lost" {
		expected := "executor_failed"
		if message.Type == "authority_lost" {
			expected = "postgres_write_fence_rejected"
		}
		if message.Error != expected {
			return executorMessage{}, errors.New("Python executor response has invalid error")
		}
		return message, nil
	}
	if message.ClaimToken == "" || message.LeaseUntilMS < 1 || message.LeaseUntilMS > maxInteger || message.Error != "" {
		return executorMessage{}, errors.New("Python executor response identity is invalid")
	}
	if message.NextReadyAtMS != nil && (*message.NextReadyAtMS < 0 || *message.NextReadyAtMS > maxInteger) {
		return executorMessage{}, errors.New("Python executor response ready time is invalid")
	}
	return message, nil
}
