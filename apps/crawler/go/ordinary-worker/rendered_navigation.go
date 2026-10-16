package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"

	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	"google.golang.org/protobuf/proto"
)

// One reservation and cleanup boundary for document monitor/detail navigation.
// Parser configuration and queue/database authority remain with their callers.
func (r *NativeRenderedDetails) executeDocumentNavigation(ctx context.Context, input *runtimev1.BrowserExecutionInput, options map[string]any, fingerprint string, pipeline []actions.Action) (*runtimev1.BrowserResult, error) {
	lease, err := bindBrowserProxy(input, options, r.proxyHTTP)
	if err != nil {
		return nil, err
	}
	defer lease.abandon()
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return nil, err
	}
	payload, marshalErr := proto.Marshal(input)
	if marshalErr != nil {
		held.Close()
		return nil, marshalErr
	}
	requestDigest := sha256.Sum256([]byte(input.Plan.Navigation.OriginRequestId))
	response, err := held.DocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: hex.EncodeToString(requestDigest[:]), ConfigFingerprint: fingerprint, Input: payload, Actions: pipeline, LargeDocument: true})
	body := response.Result
	held.Close()
	if err != nil {
		lease.observe(ctx, nil, err)
		return nil, err
	}
	result, err := executor.DecodeDocumentResult(body)
	lease.observe(ctx, result, err)
	return result, err
}
