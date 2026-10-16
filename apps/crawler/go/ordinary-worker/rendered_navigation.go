package worker

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strconv"
	"strings"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/browserproxy"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
	"google.golang.org/protobuf/proto"
)

// One reservation and cleanup boundary for document monitor/detail navigation.
// Parser configuration and queue/database authority remain with their callers.
func (r *NativeRenderedDetails) executeDocumentNavigation(ctx context.Context, input *runtimev1.BrowserExecutionInput, options map[string]any, fingerprint string, pipeline []actions.Action) (*runtimev1.BrowserResult, error) {
	return retryBrowserProxyOriginBlocks(ctx, input, options, func(attempt *runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error) {
		return r.executeDocumentNavigationOnce(ctx, attempt, options, fingerprint, pipeline)
	})
}

func retryBrowserProxyOriginBlocks(ctx context.Context, input *runtimev1.BrowserExecutionInput, options map[string]any, execute func(*runtimev1.BrowserExecutionInput) (*runtimev1.BrowserResult, error)) (*runtimev1.BrowserResult, error) {
	if options["proxy"] != true {
		return execute(input)
	}
	if value, present := options["transport_attempts"]; present && value == nil {
		return nil, queue.ErrConfiguration
	}
	attempts, err := browserproxy.OriginBlockAttempts(options["transport_attempts"])
	if err != nil || input == nil || input.Plan == nil || input.Plan.Navigation == nil || input.Plan.Navigation.OriginRequestId == "" {
		return nil, queue.ErrConfiguration
	}
	var previous *runtimev1.BrowserResult
	for attempt := 0; attempt < attempts; attempt++ {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		bound := proto.Clone(input).(*runtimev1.BrowserExecutionInput)
		if attempt > 0 {
			original := input.Plan.Navigation.OriginRequestId
			replacement := original + ":proxy-retry-" + strconv.Itoa(attempt)
			bound.Plan.Navigation.OriginRequestId = replacement
			for _, operation := range bound.Plan.OriginOperations {
				if operation == nil {
					return nil, queue.ErrConfiguration
				}
				operation.OriginRequestId = strings.Replace(operation.OriginRequestId, original, replacement, 1)
				if operation.ParentOriginRequestId != nil {
					parent := strings.Replace(*operation.ParentOriginRequestId, original, replacement, 1)
					operation.ParentOriginRequestId = &parent
				}
			}
		}
		result, err := execute(bound)
		if errors.Is(err, errProxyPoolExhausted) && previous != nil {
			return previous, nil
		}
		if err != nil {
			return nil, err
		}
		_, parseErr := executor.RenderedHTML(result, input.Plan.TargetUrl)
		var blocked *executor.NavigationHTTPError
		if !errors.As(parseErr, &blocked) || (blocked.Status != 401 && blocked.Status != 403 && blocked.Status != 429) {
			return result, nil
		}
		previous = result
	}
	return previous, nil
}

func (r *NativeRenderedDetails) executeDocumentNavigationOnce(ctx context.Context, input *runtimev1.BrowserExecutionInput, options map[string]any, fingerprint string, pipeline []actions.Action) (*runtimev1.BrowserResult, error) {
	lease, err := bindBrowserProxy(input, options, r.proxyHTTP)
	if err != nil {
		return nil, err
	}
	defer lease.abandon()
	held, err := waitRenderedReservation(ctx, r.client.Reserve)
	if err != nil {
		return nil, err
	}
	var body []byte
	if len(pipeline) > 0 {
		payload, marshalErr := proto.Marshal(input)
		if marshalErr != nil {
			held.Close()
			return nil, marshalErr
		}
		requestDigest := sha256.Sum256([]byte(input.Plan.Navigation.OriginRequestId))
		response, actionErr := held.DocumentActions(ctx, actions.Request{Protocol: actions.Protocol, RequestID: hex.EncodeToString(requestDigest[:]), ConfigFingerprint: fingerprint, Input: payload, Actions: pipeline})
		body, err = response.Result, actionErr
	} else {
		body, err = held.Execute(ctx, input)
	}
	held.Close()
	if err != nil {
		lease.observe(ctx, nil, err)
		return nil, err
	}
	result, err := executor.DecodeResult(body)
	lease.observe(ctx, result, err)
	return result, err
}
