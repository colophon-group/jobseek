package lightpandaclient

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"sync"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
)

// DayforceSession retains the same C4 reservation through all page reads and
// the final cleanup acknowledgment. It owns no queue or persistence authority.
type DayforceSession struct {
	reservation *Reservation
	request     df.Request
	sequence    uint64
	closed      bool
	stop        chan struct{}
	stopOnce    sync.Once
}

func (r *Reservation) StartDayforce(ctx context.Context, request df.Request) (*DayforceSession, *df.Ready, error) {
	if r == nil || r.connection == nil || r.used || !request.Valid() {
		return nil, nil, df.ErrProtocol
	}
	r.used = true
	s := &DayforceSession{reservation: r, request: request, stop: make(chan struct{})}
	go func() {
		select {
		case <-ctx.Done():
			r.Close()
		case <-s.stop:
		}
	}()
	deadline := time.Now().Add(time.Duration(request.TimeoutMS)*time.Millisecond + 15*time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	if err := r.connection.SetDeadline(deadline); err != nil {
		s.Close()
		return nil, nil, err
	}
	body, err := json.Marshal(request)
	if err == nil && len(body) > df.RequestLimit {
		err = df.ErrProtocol
	}
	if err == nil {
		var record []byte
		record, err = framing.EncodeRecord(body, inputFrameLimit)
		if err == nil {
			err = WriteAll(r.connection, append(record, 0))
		}
	}
	if err != nil {
		s.Close()
		return nil, nil, err
	}
	f, err := s.readFrame()
	if err != nil || f.Sequence != 0 || f.Ready == nil {
		s.Close()
		if err == nil {
			err = df.ErrProtocol
		}
		return nil, nil, err
	}
	return s, f.Ready, nil
}

func (s *DayforceSession) readFrame() (df.Frame, error) {
	var f df.Frame
	body, err := framing.ReadRecord(s.reservation.connection, df.FrameLimit)
	if err != nil {
		return f, err
	}
	if df.Decode(body, df.FrameLimit, &f) != nil || !f.Valid() || f.RequestID != s.request.RequestID {
		return df.Frame{}, df.ErrProtocol
	}
	return f, nil
}

func (s *DayforceSession) send(c df.Command) error {
	if s == nil || s.closed || !c.Valid() {
		return df.ErrProtocol
	}
	body, err := json.Marshal(c)
	if err != nil {
		return err
	}
	record, err := framing.EncodeRecord(body, df.CommandLimit)
	if err != nil {
		return err
	}
	return WriteAll(s.reservation.connection, record)
}

func (s *DayforceSession) Search(offset int) (*df.Page, error) {
	if s == nil || s.closed {
		return nil, df.ErrProtocol
	}
	s.sequence++
	if err := s.send(df.Command{Sequence: s.sequence, Offset: &offset}); err != nil {
		s.Close()
		return nil, err
	}
	f, err := s.readFrame()
	if err != nil || f.Sequence != s.sequence || f.Page == nil || f.Page.Offset != offset || f.Page.FinalURL != s.request.SearchURL() {
		s.Close()
		if err == nil {
			err = df.ErrProtocol
		}
		return nil, err
	}
	return f.Page, nil
}

// Finish returns success only after the server proves child/target cleanup and
// closes the conversation. A prefix of pages alone cannot authorize inventory.
func (s *DayforceSession) Finish(complete bool) error {
	if s == nil || s.closed {
		return df.ErrProtocol
	}
	defer s.Close()
	s.sequence++
	if err := s.send(df.Command{Sequence: s.sequence, Finish: &complete}); err != nil {
		return err
	}
	f, err := s.readFrame()
	if err != nil {
		return err
	}
	if f.Sequence != s.sequence || f.Closed == nil || f.Closed.Success != complete || complete && f.Closed.Reason != "completed" || !complete && f.Closed.Reason != "aborted" {
		return df.ErrProtocol
	}
	var one [1]byte
	n, err := s.reservation.connection.Read(one[:])
	if n != 0 || !errors.Is(err, io.EOF) {
		return df.ErrProtocol
	}
	return nil
}

func (s *DayforceSession) Close() {
	if s == nil {
		return
	}
	s.closed = true
	s.stopOnce.Do(func() { close(s.stop) })
	s.reservation.Close()
}
