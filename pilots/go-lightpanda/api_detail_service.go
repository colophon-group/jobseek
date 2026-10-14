//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func (execution *runtimeV1ServiceExecution) executeAPIDetail(ctx context.Context, request replay.Request) (replay.Response, error) {
	response := replay.Response{Protocol: replay.Protocol, RequestID: request.RequestID, ConfigFingerprint: request.ConfigFingerprint, Outcome: "failed"}
	options, err := api.BrowserDetailOptionsFromConfig(request.Metadata)
	if err != nil {
		response.Outcome = "invalid_config"
		return response, nil
	}
	wait := map[string]runtimev1.WaitCondition{"commit": runtimev1.WaitCondition_WAIT_CONDITION_COMMIT, "domcontentloaded": runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED, "load": runtimev1.WaitCondition_WAIT_CONDITION_LOAD, "networkidle": runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE}[options.Wait]
	collected := false
	var detail map[string]any
	task := Task{URL: request.BoardURL, Navigation: &navigationOptions{wait: wait, timeout: time.Duration(options.TimeoutMS) * time.Millisecond}, APIReplay: &apiReplayTask{boardURL: request.BoardURL, options: api.BrowserReplayOptions{Wait: options.Wait, TimeoutMS: options.TimeoutMS, SettleMS: options.SettleMS, Inventory: api.Options{Endpoint: request.BoardURL, Method: "GET"}}, converse: func(context.Context, api.Fetch, bool) error { return errReplayCapture }}}
	task.APIReplay.detailConverse = func(call context.Context, documents []*api.Document) error {
		document, object, err := api.FindSingleJob(call, documents, options.Path)
		if err != nil {
			return err
		}
		if object == nil {
			detail = map[string]any{}
		} else {
			detail, err = api.ProjectSingleJobDetail(document, object, options.Fields)
		}
		collected = err == nil
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(request.TimeoutMS)*time.Millisecond)
	defer cancel()
	config := execution.dayforceConfig
	config.TaskTimeout = time.Duration(request.TimeoutMS) * time.Millisecond
	result, err := execution.dayforceRun(ctx, config, task)
	if err != nil {
		var reservation *policy.Reservation
		if !errors.Is(err, errCleanupUnproved) && errors.As(err, &reservation) {
			response.Outcome = "publisher_reserved"
			response.Reservation = &replay.Reservation{URL: reservation.URL, Source: reservation.Source, PolicyURL: reservation.PolicyURL}
		}
		return response, nil
	}
	if !collected || !result.apiReplaySessionSettled || ctx.Err() != nil {
		return response, nil
	}
	body, err := json.Marshal(detail)
	if err != nil || len(body) > replay.ResponseLimit-1024 {
		return response, nil
	}
	response.Outcome = "success"
	response.Detail = body
	return response, nil
}
