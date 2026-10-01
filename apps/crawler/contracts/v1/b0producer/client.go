package b0producer

import (
	"context"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

const Timeout = 3 * time.Second

type socketIdentity struct{ device, inode uint64 }

// Client has a fixed socket/owner and no environment-controlled routing or peer
// override. Each exchange re-attests metadata and kernel peer credentials.
type Client struct {
	path  string
	owner uint32
	peer  func(*net.UnixConn) (uint32, error)
}

func NewClient() *Client { return &Client{SocketPath, ProducerUID, peerUID} }

func (c *Client) identity() (socketIdentity, error) {
	if c == nil || c.path == "" || c.peer == nil {
		return socketIdentity{}, ErrConfiguration
	}
	directory, err := os.Lstat(filepath.Dir(c.path))
	if err != nil || !directory.IsDir() || directory.Mode().Perm() != 0o700 || directory.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return socketIdentity{}, ErrAuthority
	}
	dm, ok := directory.Sys().(*syscall.Stat_t)
	if !ok || dm.Uid != c.owner {
		return socketIdentity{}, ErrAuthority
	}
	socket, err := os.Lstat(c.path)
	if err != nil || socket.Mode()&os.ModeSocket == 0 || socket.Mode().Perm() != 0o600 || socket.Mode()&(os.ModeSetuid|os.ModeSetgid|os.ModeSticky) != 0 {
		return socketIdentity{}, ErrAuthority
	}
	sm, ok := socket.Sys().(*syscall.Stat_t)
	if !ok || sm.Uid != c.owner {
		return socketIdentity{}, ErrAuthority
	}
	return socketIdentity{uint64(sm.Dev), uint64(sm.Ino)}, nil
}

func (c *Client) exchange(ctx context.Context, request Request) (Response, error) {
	if ctx == nil {
		return Response{}, ErrConfiguration
	}
	body, err := EncodeRequest(request)
	if err != nil {
		return Response{}, err
	}
	record, err := framing.EncodeRecord(body, FrameLimit)
	if err != nil {
		return Response{}, ErrConfiguration
	}
	ctx, cancel := context.WithTimeout(ctx, Timeout)
	defer cancel()
	before, err := c.identity()
	if err != nil {
		return Response{}, err
	}
	raw, err := (&net.Dialer{}).DialContext(ctx, "unix", c.path)
	if err != nil {
		return Response{}, unavailable(ctx)
	}
	connection, ok := raw.(*net.UnixConn)
	if !ok {
		_ = raw.Close()
		return Response{}, ErrAuthority
	}
	defer connection.Close()
	stop := context.AfterFunc(ctx, func() { _ = connection.Close() })
	defer stop()
	deadline, _ := ctx.Deadline()
	if connection.SetDeadline(deadline) != nil {
		return Response{}, unavailable(ctx)
	}
	uid, err := c.peer(connection)
	if err != nil || uid != c.owner {
		return Response{}, ErrAuthority
	}
	after, err := c.identity()
	if err != nil || before != after {
		return Response{}, ErrAuthority
	}
	for len(record) > 0 {
		n, err := connection.Write(record)
		if err != nil || n < 1 || n > len(record) {
			return Response{}, unavailable(ctx)
		}
		record = record[n:]
	}
	body, err = framing.ReadRecord(connection, FrameLimit)
	if err != nil {
		var invalid *framing.FramingError
		if ctx.Err() == nil && errors.As(err, &invalid) && invalid.Code != framing.CodeAmbiguousEOF {
			return Response{}, ErrProtocol
		}
		return Response{}, unavailable(ctx)
	}
	var extra [1]byte
	if n, err := connection.Read(extra[:]); n != 0 {
		return Response{}, ErrProtocol
	} else if err != io.EOF {
		return Response{}, unavailable(ctx)
	}
	if ctx.Err() != nil {
		return Response{}, ctx.Err()
	}
	return DecodeResponse(body)
}

func unavailable(ctx context.Context) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	// The socket's deadline may fire just before the context timer runs.
	if deadline, ok := ctx.Deadline(); ok && !time.Now().Before(deadline) {
		return context.DeadlineExceeded
	}
	return ErrUnavailable
}

func (c *Client) Manifest(ctx context.Context, cohort string) (Response, error) {
	r, err := c.exchange(ctx, Request{Version: Protocol, Operation: "manifest", Cohort: cohort, Config: map[string]string{}, OperatorTransfer: true})
	if err == nil && (r.Outcome != "manifest" || r.Cohort != cohort) {
		return Response{}, ErrProtocol
	}
	return r, err
}

func (c *Client) Prepare(ctx context.Context, t Task) (Response, error) {
	r, err := c.exchange(ctx, taskRequest(t, "prepare", "", true))
	if err == nil && r.Outcome != "prepared" && r.Outcome != "legacy" {
		return Response{}, ErrProtocol
	}
	return r, err
}

// Activate performs one exchange with the caller's durably approved digest.
// Ambiguous disconnects/timeouts are never retried automatically.
func (c *Client) Activate(ctx context.Context, t Task, digest string) (Response, error) {
	r, err := c.exchange(ctx, taskRequest(t, "activate", digest, true))
	if err == nil && (r.Outcome != "activated" || r.PreparationDigest != digest) {
		return Response{}, ErrProtocol
	}
	return r, err
}

func (c *Client) Enqueue(ctx context.Context, t Task) (Response, error) {
	r, err := c.exchange(ctx, taskRequest(t, "enqueue", "", false))
	if err == nil && r.Outcome != "activated" && r.Outcome != "legacy" {
		return Response{}, ErrProtocol
	}
	return r, err
}
