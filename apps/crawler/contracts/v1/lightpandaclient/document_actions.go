package lightpandaclient

import (
	"context"
	"io"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

// DocumentActions returns the existing typed document result after renderer cleanup.
func (r *Reservation) DocumentActions(ctx context.Context, request replay.Request) (replay.Response, error) {
	var response replay.Response
	if r == nil || r.connection == nil || r.used || !request.Valid() {
		return response, replay.ErrActions
	}
	r.used = true
	defer r.Close()
	stop := context.AfterFunc(ctx, func() { r.Close() })
	defer stop()
	deadline := time.Now().Add(documentActionBudget(request) + 15*time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if err := r.connection.SetDeadline(deadline); err != nil {
		return response, replay.ErrActions
	}
	body, err := replay.MarshalBounded(request)
	if err != nil {
		return response, replay.ErrActions
	}
	record, err := framing.EncodeRecord(body, replay.RequestLimit)
	if err != nil {
		return response, replay.ErrActions
	}
	if WriteAll(r.connection, append(record, 0)) != nil {
		return response, replay.ErrActions
	}
	body, err = framing.ReadRecord(r.connection, replay.ResponseLimit)
	if ctx.Err() != nil {
		return response, ctx.Err()
	}
	if err != nil || replay.Decode(body, replay.ResponseLimit, &response) != nil || !response.Valid() || response.RequestID != request.RequestID || response.ConfigFingerprint != request.ConfigFingerprint {
		return replay.Response{}, replay.ErrActions
	}
	var trailing [1]byte
	if n, err := r.connection.Read(trailing[:]); n != 0 || err != io.EOF {
		return replay.Response{}, replay.ErrActions
	}
	return response, nil
}

func documentActionBudget(request replay.Request) time.Duration {
	return 260*time.Second + replay.Budget(request.Actions)
}
