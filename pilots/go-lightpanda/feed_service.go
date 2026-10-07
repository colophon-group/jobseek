//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	feed "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/feedsession"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/resourcepolicy"
	"net"
	"time"
)

type feedServiceExecutor interface {
	executeFeed(context.Context, feed.Request, func(context.Context, feedPageFetch) (Result, error)) error
}

func (execution *runtimeV1ServiceExecution) executeFeed(ctx context.Context, r feed.Request, converse func(context.Context, feedPageFetch) (Result, error)) error {
	if !r.Valid() || converse == nil {
		return feed.ErrProtocol
	}
	config := execution.dayforceConfig
	config.TaskTimeout = time.Duration(r.TimeoutMS) * time.Millisecond
	wait := map[string]runtimev1.WaitCondition{"commit": 1, "domcontentloaded": 2, "load": 3, "networkidle": 4}[r.Wait]
	navigation := &navigationOptions{wait: wait, timeout: time.Duration(r.NavigationTimeoutMS) * time.Millisecond, transportRetries: 1}
	if r.WaitFallback != nil {
		navigation.fallback = &navigationOptions{wait: map[string]runtimev1.WaitCondition{"commit": 1, "domcontentloaded": 2, "load": 3, "networkidle": 4}[*r.WaitFallback], timeout: min(5*time.Second, navigation.timeout)}
	}
	_, err := execution.dayforceRun(ctx, config, Task{URL: r.FeedURL, ResponseBodyLimit: feed.BodyLimit, Navigation: navigation, Feed: &feedTask{request: r, converse: converse}})
	return err
}
func writeFeedControl(conn net.Conn, c feed.Control) error {
	if !c.Valid() {
		return feed.ErrProtocol
	}
	body, e := json.Marshal(c)
	if e != nil {
		return feed.ErrProtocol
	}
	record, e := framing.EncodeRecord(body, feed.CommandLimit)
	if e != nil {
		return e
	}
	_, e = writeFull(conn, record)
	return e
}

// Page frames are provisional. Only the final control frame follows cleanup.
func feedPageResult(r Result) *runtimev1.BrowserResult {
	if r.ResponseBody == nil || uint64(len(r.ResponseBody)) > feed.BodyLimit || r.Status < 100 || r.Status > 599 || !resourcepolicy.Valid(r.ResourcePolicy) {
		return runtimeV1Failure(runtimev1.ErrorCode_ERROR_CODE_INTERNAL, runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY)
	}
	manifest := func(body []byte) *runtimev1.ChunkManifest {
		total := sha256.Sum256(body)
		out := &runtimev1.ChunkManifest{Complete: true, TotalSizeBytes: uint64(len(body)), TotalSha256: hex.EncodeToString(total[:])}
		for start := 0; start < len(body); start += 64 * 1024 {
			end := min(start+64*1024, len(body))
			part := append([]byte{}, body[start:end]...)
			sum := sha256.Sum256(part)
			out.Chunks = append(out.Chunks, &runtimev1.DataChunk{Sequence: uint32(len(out.Chunks)), SizeBytes: uint64(len(part)), Sha256: hex.EncodeToString(sum[:]), Storage: &runtimev1.DataChunk_InlineBody{InlineBody: part}})
		}
		return out
	}
	status := uint32(r.Status)
	return sanitizeRuntimeV1Result(&runtimev1.BrowserResult{ContractVersion: "crawler.runtime/v1", Backend: runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA, Outcome: &runtimev1.BrowserResult_Success{Success: &runtimev1.BrowserSuccess{FinalUrl: r.FinalURL, Status: &status, ResourcePolicy: r.ResourcePolicy, Html: manifest([]byte{}), Captures: []*runtimev1.CapturedValue{{CaptureId: "feed", Body: manifest(r.ResponseBody)}}}}})
}

func (service *runtimeV1Service) handleFeed(parent context.Context, conn net.Conn, reader *bufio.Reader, r feed.Request) {
	executor, ok := service.executor.(feedServiceExecutor)
	if !ok {
		return
	}
	budget := time.Duration(r.TimeoutMS) * time.Millisecond
	_ = conn.SetDeadline(time.Now().Add(budget + 15*time.Second))
	ctx, cancel := context.WithTimeout(parent, budget)
	defer cancel()
	type commandResult struct {
		command feed.Command
		err     error
	}
	commands := make(chan commandResult, 1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		for {
			body, e := framing.ReadRecord(reader, feed.CommandLimit)
			var c feed.Command
			if e == nil && (feed.Decode(body, feed.CommandLimit, &c) != nil || !c.Valid()) {
				e = feed.ErrProtocol
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
	pageNumber := r.Start
	pages := 0
	completed, aborted := false, false
	err := executor.executeFeed(ctx, r, func(callCtx context.Context, fetch feedPageFetch) (Result, error) {
		if e := writeFeedControl(conn, feed.Control{RequestID: r.RequestID, Ready: true}); e != nil {
			return Result{}, e
		}
		for {
			var next commandResult
			select {
			case next = <-commands:
			case <-callCtx.Done():
				return Result{}, callCtx.Err()
			}
			if next.err != nil {
				return Result{}, next.err
			}
			c := next.command
			if c.Sequence != sequence+1 {
				return Result{}, feed.ErrProtocol
			}
			sequence = c.Sequence
			if c.Finish != nil {
				if *c.Finish && pages == 0 {
					return Result{}, feed.ErrProtocol
				}
				completed, aborted = *c.Finish, !*c.Finish
				return Result{feedSessionSettled: true}, nil
			}
			if *c.Page != pageNumber || pages >= r.MaxPages {
				return Result{}, feed.ErrProtocol
			}
			endpoint, e := r.PageURL(pageNumber)
			if e != nil {
				return Result{}, e
			}
			result, e := fetch(callCtx, endpoint)
			if e != nil {
				code, disposition := runtimev1.ErrorCode_ERROR_CODE_TRANSPORT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_RETRY_POLICY
				if errors.Is(e, errResourceLimit) {
					code, disposition = runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT, runtimev1.ErrorDisposition_ERROR_DISPOSITION_DEFER_POLICY
				}
				_ = writeRuntimeV1Result(conn, runtimeV1Failure(code, disposition))
				return Result{}, e
			}
			if e = writeRuntimeV1Result(conn, feedPageResult(result)); e != nil {
				return Result{}, e
			}
			pages++
			pageNumber += r.Increment
		}
	})
	_ = conn.SetReadDeadline(time.Now())
	cancel()
	<-done
	reason := "failed"
	success := err == nil && completed
	if success {
		reason = "completed"
	} else if err == nil && aborted {
		reason = "aborted"
	}
	_ = writeFeedControl(conn, feed.Control{RequestID: r.RequestID, Sequence: sequence, Closed: &feed.Closed{Success: success, Reason: reason}})
}
