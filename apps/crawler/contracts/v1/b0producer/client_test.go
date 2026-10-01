package b0producer

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

type socketFixture struct {
	client   *Client
	listener *net.UnixListener
	requests chan Request
	count    atomic.Int32
	done     chan struct{}
}

// Only tests may select a private temporary socket/current UID. On Linux the
// client still uses actual SO_PEERCRED; Darwin cannot attest production peers.
func privateSocket(t *testing.T, reply func(*net.UnixConn, Request)) *socketFixture {
	t.Helper()
	directory, err := os.MkdirTemp("/tmp", "b0uds-")
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
	if err := os.Chmod(path, 0o600); err != nil {
		t.Fatal(err)
	}
	peer := peerUID
	if runtime.GOOS != "linux" {
		peer = func(*net.UnixConn) (uint32, error) { return uint32(os.Getuid()), nil }
	}
	f := &socketFixture{client: &Client{path, uint32(os.Getuid()), peer}, listener: listener, requests: make(chan Request, 16), done: make(chan struct{})}
	go func() {
		defer close(f.done)
		for {
			conn, err := listener.AcceptUnix()
			if err != nil {
				return
			}
			f.count.Add(1)
			_ = conn.SetDeadline(time.Now().Add(2 * Timeout))
			body, err := framing.ReadRecord(conn, FrameLimit)
			if err == nil {
				var request Request
				if json.Unmarshal(body, &request) == nil {
					f.requests <- request
					reply(conn, request)
				}
			}
			_ = conn.Close()
		}
	}()
	t.Cleanup(func() {
		_ = listener.Close()
		select {
		case <-f.done:
		case <-time.After(2 * Timeout):
			t.Error("private producer did not drain")
		}
	})
	return f
}

func manifestResponse() Response {
	return Response{Version: Protocol, Outcome: "manifest", Reason: "manifest", Cohort: "c1", BoardSlugs: []string{"browser-use-careers"}, LifetimeCapacity: LifetimeCapacity, LifetimeHeadroom: LifetimeCapacity}
}

func responseRecord(t *testing.T, response Response) []byte {
	t.Helper()
	body, err := CanonicalResponse(response)
	if err != nil {
		t.Fatal(err)
	}
	record, err := framing.EncodeRecord(body, FrameLimit)
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func TestOwnedSocketExactExchangeAndKernelPeer(t *testing.T) {
	record := responseRecord(t, manifestResponse())
	f := privateSocket(t, func(c *net.UnixConn, _ Request) { // Force incremental prefix/body reads.
		for _, b := range record {
			if _, err := c.Write([]byte{b}); err != nil {
				return
			}
		}
	})
	got, err := f.client.Manifest(context.Background(), "c1")
	if err != nil || got.Outcome != "manifest" {
		t.Fatal("actual socket exchange rejected", err)
	}
	r := <-f.requests
	if r.Version != Protocol || r.Operation != "manifest" || r.Cohort != "c1" || !r.OperatorTransfer || f.count.Load() != 1 {
		t.Fatal("socket request lost protocol/operation binding")
	}
	if runtime.GOOS != "linux" {
		conn, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: f.client.path, Net: "unix"})
		if err != nil {
			t.Fatal(err)
		}
		_, err = peerUID(conn)
		_ = conn.Close()
		if !errors.Is(err, ErrAuthority) {
			t.Fatal("non-Linux production peer granted authority")
		}
	}
	production := NewClient()
	if production.path != SocketPath || production.owner != ProducerUID {
		t.Fatal("production authority became configurable")
	}
}

func TestSocketAuthorityRejectsBeforeSendingRequest(t *testing.T) {
	for _, fault := range []string{"directory_mode", "directory_sticky", "socket_mode", "directory_symlink", "socket_symlink", "wrong_owner", "wrong_peer", "peer_error", "socket_replaced"} {
		t.Run(fault, func(t *testing.T) {
			f := privateSocket(t, func(*net.UnixConn, Request) {})
			switch fault {
			case "directory_mode":
				_ = os.Chmod(filepath.Dir(f.client.path), 0o755)
			case "directory_sticky":
				_ = os.Chmod(filepath.Dir(f.client.path), 0o700|os.ModeSticky)
			case "socket_mode":
				_ = os.Chmod(f.client.path, 0o666)
			case "wrong_owner":
				f.client.owner++
			case "directory_symlink":
				link := filepath.Dir(f.client.path) + "-link"
				if err := os.Symlink(filepath.Dir(f.client.path), link); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Remove(link) })
				f.client.path = filepath.Join(link, "control.sock")
			case "socket_symlink":
				link := filepath.Join(filepath.Dir(f.client.path), "other.sock")
				if err := os.Symlink(f.client.path, link); err != nil {
					t.Fatal(err)
				}
				f.client.path = link
			case "wrong_peer":
				f.client.peer = func(*net.UnixConn) (uint32, error) { return uint32(os.Getuid()) + 1, nil }
			case "peer_error":
				f.client.peer = func(*net.UnixConn) (uint32, error) { return 0, errors.New("secret peer failure") }
			case "socket_replaced":
				originalPeer := f.client.peer
				f.client.peer = func(c *net.UnixConn) (uint32, error) {
					uid, err := originalPeer(c)
					if err != nil {
						return uid, err
					}
					if err := os.Remove(f.client.path); err != nil {
						return 0, err
					}
					other, err := net.ListenUnix("unix", &net.UnixAddr{Name: f.client.path, Net: "unix"})
					if err != nil {
						return 0, err
					}
					defer other.Close()
					other.SetUnlinkOnClose(false)
					if err := os.Chmod(f.client.path, 0o600); err != nil {
						return 0, err
					}
					return uid, nil
				}
			}
			_, err := f.client.Manifest(context.Background(), "c1")
			if !errors.Is(err, ErrAuthority) || strings.Contains(err.Error(), "secret") {
				t.Fatal("unsafe socket authority accepted/exposed", err)
			}
			select {
			case <-f.requests:
				t.Fatal("unauthenticated request was sent")
			default:
			}
		})
	}
}

func TestSocketRefusesMalformedFramesAndOperationMismatch(t *testing.T) {
	valid := responseRecord(t, manifestResponse())
	oversized := make([]byte, binary.MaxVarintLen64)
	oversized = oversized[:binary.PutUvarint(oversized, FrameLimit)]
	for _, test := range []struct {
		name   string
		record []byte
		wanted error
	}{
		{"trailing", append(append([]byte{}, valid...), 0), ErrProtocol},
		{"nonminimal", []byte{0x80, 0}, ErrProtocol},
		{"oversized", oversized, ErrProtocol},
		{"truncated", valid[:len(valid)-1], ErrUnavailable},
		{"disconnect", nil, ErrUnavailable},
		{"empty_payload", []byte{0}, ErrProtocol},
	} {
		t.Run(test.name, func(t *testing.T) {
			f := privateSocket(t, func(c *net.UnixConn, _ Request) { _, _ = c.Write(test.record) })
			_, err := f.client.Manifest(context.Background(), "c1")
			if !errors.Is(err, test.wanted) || f.count.Load() != 1 {
				t.Fatal("unsafe or retried frame", err)
			}
		})
	}
	for _, fault := range []string{"cohort", "outcome", "activation_digest"} {
		t.Run(fault, func(t *testing.T) {
			r := manifestResponse()
			if fault == "cohort" {
				r.Cohort = "c2"
			} else {
				r.Cohort = ""
				r.BoardSlugs = []string{}
				r.Outcome = "activated"
				r.Reason = "activated"
				r.Activated = true
				r.PreparationDigest = strings.Repeat("b", 64)
				r.PayloadSHA256 = strings.Repeat("c", 64)
			}
			record := responseRecord(t, r)
			f := privateSocket(t, func(c *net.UnixConn, _ Request) { _, _ = c.Write(record) })
			var err error
			if fault == "activation_digest" {
				_, err = f.client.Activate(context.Background(), Task{Domain: "jobs.example.test", PostingID: "task", Config: map[string]string{}}, strings.Repeat("a", 64))
			} else {
				_, err = f.client.Manifest(context.Background(), "c1")
			}
			if !errors.Is(err, ErrProtocol) {
				t.Fatal("unbound producer response accepted", err)
			}
		})
	}
}

func TestAmbiguousActivationCancellationAndDeadlineNeverRetry(t *testing.T) {
	for _, fault := range []string{"deadline", "cancel", "exact_eof_required", "client_timeout"} {
		t.Run(fault, func(t *testing.T) {
			digest := strings.Repeat("a", 64)
			r := Response{Version: Protocol, Outcome: "activated", Reason: "activated", PreparationDigest: digest, PayloadSHA256: strings.Repeat("b", 64), Activated: true, BoardSlugs: []string{}, LifetimeCapacity: LifetimeCapacity, LifetimeHeadroom: LifetimeCapacity}
			record := responseRecord(t, r)
			f := privateSocket(t, func(c *net.UnixConn, _ Request) {
				if fault == "exact_eof_required" {
					_, _ = c.Write(record)
				}
				var one [1]byte
				_, _ = c.Read(one[:]) // Wait for client close, rather than sleep.
			})
			ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
			defer cancel()
			if fault == "client_timeout" {
				ctx = context.Background()
			}
			if fault == "cancel" {
				go func() { <-f.requests; cancel() }()
			}
			began := time.Now()
			_, err := f.client.Activate(ctx, Task{Domain: "jobs.example.test", PostingID: "task", Config: map[string]string{}}, digest)
			if fault == "cancel" {
				if !errors.Is(err, context.Canceled) {
					t.Fatal("cancellation lost", err)
				}
			} else if !errors.Is(err, context.DeadlineExceeded) {
				t.Fatal("deadline lost", err)
			}
			if f.count.Load() != 1 || time.Since(began) > Timeout+time.Second {
				t.Fatal("ambiguous activation retried or unbounded")
			}
		})
	}
}

// EOF must follow one complete response; a producer that merely half-closes
// its write side still provides exact EOF and must be accepted.
func TestSocketAcceptsExactWriteHalfClose(t *testing.T) {
	record := responseRecord(t, manifestResponse())
	f := privateSocket(t, func(c *net.UnixConn, _ Request) {
		_, _ = c.Write(record)
		_ = c.CloseWrite()
		_, _ = io.Copy(io.Discard, c)
	})
	if _, err := f.client.Manifest(context.Background(), "c1"); err != nil {
		t.Fatal("exact EOF rejected", err)
	}
}
