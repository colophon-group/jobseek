//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"net"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

type apiReplayServiceExecutor interface {
	executeAPIReplay(context.Context, replay.Request) (replay.Response, error)
}

func (execution *runtimeV1ServiceExecution) executeAPIReplay(ctx context.Context, request replay.Request) (replay.Response, error) {
	response := replay.Response{Protocol: replay.Protocol, RequestID: request.RequestID, ConfigFingerprint: request.ConfigFingerprint, Outcome: "failed"}
	if !request.Valid() {
		return response, replay.ErrProtocol
	}
	var task Task
	var inventory api.Inventory
	collected := false
	partial := false
	var gone *replayStatusError
	constructor := newAPIReplayTask
	if request.Provider != "" {
		constructor = func(board, metadata string, converse func(context.Context, api.Fetch, bool) error) (Task, error) {
			return newNativeBrowserTask(request.Provider, board, metadata, converse)
		}
	}
	task, err := constructor(request.BoardURL, string(request.Metadata), func(ctx context.Context, fetch api.Fetch, usingHTTP bool) error {
		var err error
		if request.Provider == "darwinbox" {
			board, e := api.DarwinboxBoardFromURL(task.APIReplay.boardURL)
			if e != nil {
				return e
			}
			inventory, err = api.DiscoverDarwinbox(ctx, board, fetch, func(s string) (*string, error) { return &s, nil }, nil)
			if err != nil && (!replayTerminalError(err) || errors.Is(err, context.DeadlineExceeded) && ctx.Err() == nil) {
				if len(inventory.Jobs) > 0 {
					partial = true
					collected = true
					return nil
				}
				if errors.As(err, &gone) && (gone.status == 404 || gone.status == 410) {
					collected = true
					return nil
				}
			}
		} else if request.Provider == "bytedance" {
			portal, e := api.ByteDanceOptionsFromURL(task.APIReplay.boardURL)
			if e != nil {
				return e
			}
			inventory, err = api.DiscoverByteDance(ctx, portal, fetch)
		} else {
			inventory, err = api.DiscoverBrowserReplay(ctx, task.APIReplay.options, fetch, api.PythonJoinURL, usingHTTP)
		}
		collected = err == nil
		return err
	})
	if err != nil {
		response.Outcome = "invalid_config"
		return response, nil
	}
	config := execution.dayforceConfig
	config.TaskTimeout = time.Duration(request.TimeoutMS) * time.Millisecond
	// The runner returns only after its existing child-group/port cleanup proof.
	result, err := execution.dayforceRun(ctx, config, task)
	if err != nil {
		var reservation *policy.Reservation
		if !errors.Is(err, errCleanupUnproved) && errors.As(err, &reservation) {
			response.Outcome = "publisher_reserved"
			response.Reservation = &replay.Reservation{URL: reservation.URL, Source: reservation.Source, PolicyURL: reservation.PolicyURL}
		}
		return response, nil
	}
	if !collected || !result.apiReplaySessionSettled {
		return response, nil
	}
	if gone != nil {
		board, e := api.DarwinboxBoardFromURL(task.APIReplay.boardURL)
		if e != nil {
			return response, nil
		}
		response.Outcome = "provider_gone"
		response.FailureURL = board.JobsURL()
		response.FailureStatus = gone.status
		return response, nil
	}
	body, err := json.Marshal(inventory)
	if err != nil || len(body) > replay.ResponseLimit-1024 {
		return response, nil
	}
	response.Outcome = "success"
	if partial {
		response.Outcome = "partial"
	}
	response.Inventory = body
	return response, nil
}

func (service *runtimeV1Service) handleAPIReplay(parent context.Context, conn net.Conn, reader *bufio.Reader, request replay.Request) {
	executor, ok := service.executor.(apiReplayServiceExecutor)
	if !ok {
		return
	}
	budget := time.Duration(request.TimeoutMS) * time.Millisecond
	_ = conn.SetDeadline(time.Now().Add(budget + 15*time.Second))
	ctx, cancel := context.WithTimeout(parent, budget)
	done := make(chan runtimeV1PostMarkerRead, 1)
	go watchRuntimeV1PostMarker(reader, cancel, done)
	response, err := executor.executeAPIReplay(ctx, request)
	_ = conn.SetReadDeadline(time.Now())
	postMarker := <-done
	cancel()
	if postMarker.peerGone || postMarker.trailing || err != nil || !response.Valid() {
		return
	}
	body, err := json.Marshal(response)
	if err != nil {
		return
	}
	record, err := framing.EncodeRecord(body, replay.ResponseLimit)
	if err != nil {
		return
	}
	_, _ = writeFull(conn, record)
}
