package lightpandaclient

import (
	"context"
	"encoding/json"
	"errors"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	"io"
	"sync"
	"time"
)

type FeedSession struct {
	reservation *Reservation
	request     feed.Request
	sequence    uint64
	page        int
	closed      bool
	stop        chan struct{}
	stopOnce    sync.Once
}

func (r *Reservation) StartFeed(ctx context.Context, request feed.Request) (*FeedSession, error) {
	if r == nil || r.connection == nil || r.used || !request.Valid() {
		return nil, feed.ErrProtocol
	}
	r.used = true
	s := &FeedSession{reservation: r, request: request, page: request.Start, stop: make(chan struct{})}
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
	if e := r.connection.SetDeadline(deadline); e != nil {
		s.Close()
		return nil, e
	}
	body, e := json.Marshal(request)
	if e != nil {
		s.Close()
		return nil, e
	}
	record, e := framing.EncodeRecord(body, inputFrameLimit)
	if e == nil {
		e = WriteAll(r.connection, append(record, 0))
	}
	if e != nil {
		s.Close()
		return nil, e
	}
	c, e := s.control()
	if e != nil || !c.Ready || c.Sequence != 0 {
		s.Close()
		return nil, feed.ErrProtocol
	}
	return s, nil
}
func (s *FeedSession) control() (feed.Control, error) {
	var c feed.Control
	body, e := framing.ReadRecord(s.reservation.connection, feed.CommandLimit)
	if e != nil {
		return c, e
	}
	if feed.Decode(body, feed.CommandLimit, &c) != nil || !c.Valid() || c.RequestID != s.request.RequestID {
		return c, feed.ErrProtocol
	}
	return c, nil
}
func (s *FeedSession) send(c feed.Command) error {
	if s == nil || s.closed || !c.Valid() {
		return feed.ErrProtocol
	}
	body, e := json.Marshal(c)
	if e != nil {
		return e
	}
	record, e := framing.EncodeRecord(body, feed.CommandLimit)
	if e != nil {
		return e
	}
	return WriteAll(s.reservation.connection, record)
}

// A page remains provisional until Finish confirms authoritative cleanup.
func (s *FeedSession) Page(page int) ([]byte, error) {
	if s == nil || s.closed || page != s.page {
		return nil, feed.ErrProtocol
	}
	if _, e := s.request.PageURL(page); e != nil {
		return nil, e
	}
	s.sequence++
	if e := s.send(feed.Command{Sequence: s.sequence, Page: &page}); e != nil {
		s.Close()
		return nil, e
	}
	body, e := framing.ReadRecord(s.reservation.connection, resultFrameLimit)
	if e != nil {
		s.Close()
		return nil, e
	}
	s.page += s.request.Increment
	return body, nil
}
func (s *FeedSession) Finish(complete bool) error {
	if s == nil || s.closed {
		return feed.ErrProtocol
	}
	defer s.Close()
	s.sequence++
	if e := s.send(feed.Command{Sequence: s.sequence, Finish: &complete}); e != nil {
		return e
	}
	c, e := s.control()
	if e != nil {
		return e
	}
	if c.Sequence != s.sequence || c.Closed == nil || c.Closed.Success != complete || complete && c.Closed.Reason != "completed" || !complete && c.Closed.Reason != "aborted" {
		return feed.ErrProtocol
	}
	var one [1]byte
	n, e := s.reservation.connection.Read(one[:])
	if n != 0 || !errors.Is(e, io.EOF) {
		return feed.ErrProtocol
	}
	return nil
}
func (s *FeedSession) Close() {
	if s == nil {
		return
	}
	s.closed = true
	s.stopOnce.Do(func() { close(s.stop) })
	s.reservation.Close()
}
