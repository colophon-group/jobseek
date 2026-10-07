package lightpandaclient

import (
	"context"
	"encoding/json"
	"io"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

// APIReplay returns a whole inventory only after renderer cleanup. Captured
// request headers and cookies never leave the renderer's local controller.
func (r *Reservation) APIReplay(ctx context.Context, request replay.Request) (replay.Response, error) {
	var response replay.Response
	if r == nil || r.connection == nil || r.used || !request.Valid() {
		return response, replay.ErrProtocol
	}
	r.used = true
	defer r.Close()
	stop := context.AfterFunc(ctx, func() { r.Close() })
	defer stop()
	deadline := time.Now().Add(time.Duration(request.TimeoutMS)*time.Millisecond + 15*time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if err := r.connection.SetDeadline(deadline); err != nil {
		return response, replay.ErrProtocol
	}
	body, err := json.Marshal(request)
	if err != nil {
		return response, replay.ErrProtocol
	}
	record, err := framing.EncodeRecord(body, replay.RequestLimit)
	if err != nil {
		return response, replay.ErrProtocol
	}
	if WriteAll(r.connection, append(record, 0)) != nil {
		return response, replay.ErrProtocol
	}
	body, err = framing.ReadRecord(r.connection, replay.ResponseLimit)
	if ctx.Err() != nil {
		return response, ctx.Err()
	}
	if err != nil || replay.Decode(body, replay.ResponseLimit, &response) != nil || !response.Valid() || response.RequestID != request.RequestID || response.ConfigFingerprint != request.ConfigFingerprint {
		return replay.Response{}, replay.ErrProtocol
	}
	var trailing [1]byte
	if n, err := r.connection.Read(trailing[:]); n != 0 || err != io.EOF {
		return replay.Response{}, replay.ErrProtocol
	}
	return response, nil
}
