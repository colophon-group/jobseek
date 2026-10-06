//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"net"
	"time"

	df "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/dayforcesession"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

type dayforceServiceExecutor interface {
	executeDayforce(context.Context, df.Request, dayforceConversation) error
}

func writeDayforceFrame(conn net.Conn, frame df.Frame) error {
	if !frame.Valid() {
		return df.ErrProtocol
	}
	body, err := json.Marshal(frame)
	if err != nil {
		return df.ErrProtocol
	}
	record, err := framing.EncodeRecord(body, df.FrameLimit)
	if err != nil {
		return df.ErrProtocol
	}
	_, err = writeFull(conn, record)
	return err
}

// One reserved C4 slot, one bounded reader and one live target. The reader
// detects disconnect/cancellation during CDP fetch without a polling goroutine
// or an additional connection. Result success follows authoritative cleanup.
func (service *runtimeV1Service) handleDayforce(parent context.Context, conn net.Conn, reader *bufio.Reader, request df.Request) {
	executor, ok := service.executor.(dayforceServiceExecutor)
	if !ok {
		return
	}
	budget := durationDayforce(request.TimeoutMS)
	_ = conn.SetDeadline(time.Now().Add(budget + 15*time.Second))
	ctx, cancel := context.WithTimeout(parent, budget)
	defer cancel()
	type commandResult struct {
		command df.Command
		err     error
	}
	commands := make(chan commandResult, 1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			body, e := framing.ReadRecord(reader, df.CommandLimit)
			var c df.Command
			if e == nil && (df.Decode(body, df.CommandLimit, &c) != nil || !c.Valid()) {
				e = df.ErrProtocol
			}
			select {
			case commands <- commandResult{c, e}:
			case <-ctx.Done():
				return
			}
			if e != nil {
				cancel()
				return
			}
		}
	}()
	sequence := uint64(0)
	completed, aborted, invalid := false, false, false
	lastOffset, repeats := -1, 0
	err := executor.executeDayforce(ctx, request, func(converseCtx context.Context, ready df.Ready, fetch dayforceFetch) error {
		if e := writeDayforceFrame(conn, df.Frame{RequestID: request.RequestID, Sequence: 0, Ready: &ready}); e != nil {
			return e
		}
		for {
			var next commandResult
			select {
			case next = <-commands:
			case <-converseCtx.Done():
				return converseCtx.Err()
			}
			if next.err != nil {
				return next.err
			}
			c := next.command
			if c.Sequence != sequence+1 {
				invalid = true
				return df.ErrProtocol
			}
			sequence = c.Sequence
			if c.Finish != nil {
				completed = *c.Finish
				aborted = !*c.Finish
				return nil
			}
			offset := *c.Offset
			// Three ordinary fetch attempts plus one premature-empty retry,
			// itself subject to the same three-attempt transport policy.
			if lastOffset < 0 {
				if offset != 0 {
					invalid = true
					return df.ErrProtocol
				}
				lastOffset = offset
				repeats = 1
			} else if offset == lastOffset {
				repeats++
				if repeats > 6 {
					invalid = true
					return df.ErrProtocol
				}
			} else if offset == lastOffset+25-request.OffsetOverlap {
				lastOffset = offset
				repeats = 1
			} else {
				invalid = true
				return df.ErrProtocol
			}
			page, e := fetch(converseCtx, offset)
			if e != nil {
				return e
			}
			if page.Offset != offset || page.FinalURL != request.SearchURL() {
				return df.ErrProtocol
			}
			if e = writeDayforceFrame(conn, df.Frame{RequestID: request.RequestID, Sequence: sequence, Page: &page}); e != nil {
				return e
			}
		}
	})
	// Join the sole reader before closing the application conversation. The
	// runner has already disposed the target/child before returning above.
	_ = conn.SetReadDeadline(time.Now())
	cancel()
	<-done
	reason := "session"
	success := err == nil && completed
	switch {
	case errors.Is(err, errCleanupUnproved):
		reason = "cleanup"
	case invalid:
		reason = "invalid_command"
	case success:
		reason = "completed"
	case err == nil && aborted:
		reason = "aborted"
	case errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded):
		reason = "cancelled"
	case errors.Is(err, errResourceLimit):
		reason = "resource_limit"
	}
	_ = writeDayforceFrame(conn, df.Frame{RequestID: request.RequestID, Sequence: sequence, Closed: &df.Closed{Success: success, Reason: reason}})
}

func (execution *runtimeV1ServiceExecution) executeDayforce(ctx context.Context, request df.Request, converse dayforceConversation) error {
	if !request.Valid() || converse == nil || execution.dayforceRun == nil {
		return errDayforceSession
	}
	config := execution.dayforceConfig
	config.TaskTimeout = durationDayforce(request.TimeoutMS)
	task := Task{URL: request.TargetURL, Navigation: &navigationOptions{wait: runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, timeout: durationDayforce(60000)}, Dayforce: &dayforceTask{request: request, converse: converse}}
	_, err := execution.dayforceRun(ctx, config, task)
	if errors.Is(err, errCleanupUnproved) {
		execution.reportFatal(errCleanupUnproved)
	}
	return err
}
