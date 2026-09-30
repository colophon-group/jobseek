package executor

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

const (
	SocketPath           = "/run/jobseek-lightpanda-executor/executor.sock"
	TaskCapacity         = 4
	AttestationCapacity  = 1
	FirstFrameTimeout    = time.Second
	AuthorizationTimeout = 5 * time.Second
	CommitTimeout        = 15 * time.Second
)

type EpochAttestor interface {
	AttestEpoch(context.Context, int64) error
}
type TaskHandler func(context.Context, *net.UnixConn, Request) error

// Server owns only the local conversation admission. A complete native task
// handler must prove canonical task identity before requesting authorization.
type Server struct {
	Epoch int64
	Shard string
	Store EpochAttestor
	Task  TaskHandler
}

func (s Server) Serve(ctx context.Context) error {
	if s.Shard != "lightpanda-b0" || s.Epoch < 1 || s.Epoch > maxIdentityInteger || s.Store == nil || s.Task == nil {
		return ErrProtocol
	}
	return s.serve(ctx, SocketPath, executorPeerUID)
}

func privateSocketDirectory(path string) error {
	info, err := os.Lstat(filepath.Dir(path))
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0o700 {
		return ErrProtocol
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return ErrProtocol
	}
	return nil
}

func privateSocket(path string) error {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSocket == 0 || info.Mode().Perm() != 0o600 {
		return ErrProtocol
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || stat.Uid != uint32(os.Geteuid()) {
		return ErrProtocol
	}
	return nil
}

func (s Server) serve(ctx context.Context, path string, peerUID func(*net.UnixConn) (uint32, error)) error {
	if err := privateSocketDirectory(path); err != nil {
		return err
	}
	if _, err := os.Lstat(path); err == nil {
		if err := privateSocket(path); err != nil {
			return err
		}
		if err := os.Remove(path); err != nil {
			return err
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := s.Store.AttestEpoch(ctx, s.Epoch); err != nil {
		return err
	}
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return err
	}
	listener.SetUnlinkOnClose(false)
	defer listener.Close()
	if err := os.Chmod(path, 0o600); err != nil {
		return err
	}
	if err := privateSocket(path); err != nil {
		return err
	}
	// Remove only this exact socket inode, never a replacement installed by
	// another process while this service is shutting down.
	owned, err := os.Lstat(path)
	if err != nil {
		return err
	}
	defer func() {
		if now, err := os.Lstat(path); err == nil && os.SameFile(owned, now) {
			_ = os.Remove(path)
		}
	}()
	stopAccept := context.AfterFunc(ctx, func() { _ = listener.Close() })
	defer stopAccept()
	tasks := make(chan struct{}, TaskCapacity)
	attestations := make(chan struct{}, AttestationCapacity)
	// Pending first frames have their own short admission budget. They do not
	// consume task or health slots; every accepted socket is bounded to one second.
	classifying := make(chan struct{}, 64)
	conversations, cancel := context.WithCancel(context.Background())
	defer cancel()
	var handlers sync.WaitGroup
	var acceptError error
	for {
		conn, err := listener.AcceptUnix()
		if err != nil {
			if ctx.Err() == nil {
				acceptError = err
			}
			break
		}
		select {
		case classifying <- struct{}{}:
		default:
			_ = conn.Close()
			continue
		}
		handlers.Add(1)
		go func() {
			defer handlers.Done()
			defer conn.Close()
			classificationReleased := false
			defer func() {
				if !classificationReleased {
					<-classifying
				}
			}()
			uid, err := peerUID(conn)
			if err != nil || uid != uint32(os.Geteuid()) {
				return
			}
			_ = conn.SetDeadline(time.Now().Add(FirstFrameTimeout))
			payload, err := ReadFrame(conn)
			<-classifying
			classificationReleased = true
			if err != nil {
				sendExecutorError(conn)
				return
			}
			var kind struct {
				Type string `json:"type"`
			}
			_ = json.Unmarshal(payload, &kind)
			slots := tasks
			if kind.Type == "attest_route" {
				slots = attestations
			}
			select {
			case slots <- struct{}{}:
			default:
				return
			}
			defer func() { <-slots }()
			deadline := AuthorizationTimeout + CommitTimeout
			if kind.Type == "attest_route" {
				deadline = 3 * time.Second
			}
			conversation, stop := context.WithTimeout(conversations, deadline)
			defer stop()
			interrupt := context.AfterFunc(conversation, func() { _ = conn.Close() })
			defer interrupt()
			_ = conn.SetDeadline(time.Now().Add(deadline))
			if kind.Type == "attest_route" {
				err = DecodeAttestation(payload, s.Shard, s.Epoch)
				if err == nil {
					err = s.Store.AttestEpoch(conversation, s.Epoch)
				}
				if err == nil {
					err = WriteMessage(conn, map[string]any{"type": "route_attested", "shard_id": s.Shard, "routing_epoch": s.Epoch})
				}
			} else {
				var request Request
				request, err = DecodeRequest(payload)
				if err == nil {
					err = s.Task(conversation, conn, request)
				}
			}
			if errors.Is(err, ErrAuthorityLost) {
				_ = WriteMessage(conn, map[string]any{"type": "authority_lost", "error": "postgres_write_fence_rejected"})
			} else if err != nil {
				sendExecutorError(conn)
			}
		}()
	}
	finished := make(chan struct{})
	go func() { handlers.Wait(); close(finished) }()
	timer := time.NewTimer(CommitTimeout)
	defer timer.Stop()
	select {
	case <-finished:
		return acceptError
	case <-timer.C:
		cancel()
		return errors.New("executor handlers exceeded shutdown grace")
	}
}

func sendExecutorError(conn *net.UnixConn) {
	_ = conn.SetWriteDeadline(time.Now().Add(time.Second))
	_ = WriteMessage(conn, map[string]any{"type": "error", "error": "executor_failed"})
}
