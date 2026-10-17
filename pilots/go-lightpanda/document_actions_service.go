//go:build !densitybench

package main

import (
	"bufio"
	"context"
	"encoding/json"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
	"google.golang.org/protobuf/proto"
	"net"
	"time"
)

type documentActionsServiceExecutor interface {
	executeDocumentActions(context.Context, actions.Request) (actions.Response, error)
}

func (execution *runtimeV1ServiceExecution) executeDocumentActions(ctx context.Context, request actions.Request) (actions.Response, error) {
	response := actions.Response{Protocol: actions.Protocol, RequestID: request.RequestID, ConfigFingerprint: request.ConfigFingerprint}
	if !request.Valid() {
		return response, actions.ErrActions
	}
	input := new(runtimev1.BrowserExecutionInput)
	if proto.Unmarshal(request.Input, input) != nil {
		return response, actions.ErrActions
	}
	// Reuse the same strict navigation adapter and cleanup sanitizer. Actions are
	// attached only after navigation input validation, and never to B1 evaluation.
	var identities []string
	constructor := lightpandaadapter.NewDocumentSessionRenderOnly
	if request.LargeDocument {
		constructor = lightpandaadapter.NewLargeDocumentSessionRenderOnly
	}
	adapter, err := constructor(runtimeV1Runner{config: execution.dayforceConfig, run: execution.dayforceRun, documentActions: append([]actions.Action(nil), request.Actions...), inlineCards: request.InlineCards, inlineIdentities: &identities, largeDocument: request.LargeDocument}, request.Actions, request.InlineCards, len(execution.dayforceConfig.proxyEndpoints) > 0)
	if err != nil {
		return response, err
	}
	htmlLimit := lightpandaadapter.HTMLPayloadLimit
	if request.LargeDocument {
		htmlLimit = actions.LargeDocumentHTMLLimit
	}
	result := sanitizeRuntimeV1ResultWithHTMLLimit(adapter.Execute(ctx, input), htmlLimit)
	if result.GetSuccess() != nil {
		response.InlineIdentities = identities
	}
	response.Result, err = proto.MarshalOptions{Deterministic: true}.Marshal(result)
	if err != nil || !response.Valid() || len(response.Result) > request.ResultLimit() {
		return actions.Response{}, actions.ErrActions
	}
	return response, nil
}
func (service *runtimeV1Service) handleDocumentActions(parent context.Context, conn net.Conn, reader *bufio.Reader, request actions.Request) {
	executor, ok := service.executor.(documentActionsServiceExecutor)
	if !ok {
		return
	}
	budget := 260*time.Second + actions.Budget(request.Actions) + inlineCardBudget(request.InlineCards)
	_ = conn.SetDeadline(time.Now().Add(budget + 15*time.Second))
	ctx, cancel := context.WithTimeout(parent, budget)
	done := make(chan runtimeV1PostMarkerRead, 1)
	go watchRuntimeV1PostMarker(reader, cancel, done)
	response, err := executor.executeDocumentActions(ctx, request)
	_ = conn.SetReadDeadline(time.Now())
	post := <-done
	cancel()
	if post.peerGone || post.trailing || err != nil || !response.Valid() || len(response.Result) > request.ResultLimit() {
		return
	}
	body, err := json.Marshal(response)
	if err != nil {
		return
	}
	record, err := framing.EncodeRecord(body, request.ResponseLimit())
	if err != nil {
		return
	}
	_, _ = writeFull(conn, record)
}
