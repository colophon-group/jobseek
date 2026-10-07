//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
	policy "github.com/colophon-group/jobseek/apps/crawler/go/publisher-policy"
)

func TestAPIReplayServiceCannotPublishBeforeCleanupOrAfterPartialFailure(t *testing.T) {
	request := replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://example.com/careers", Metadata: json.RawMessage(replayControllerMetadata), TimeoutMS: 15000}
	for _, mode := range []string{"success", "cleanup", "publisher", "publisher-cleanup"} {
		t.Run(mode, func(t *testing.T) {
			cleaned := false
			execution := &runtimeV1ServiceExecution{dayforceRun: func(ctx context.Context, _ Config, task Task) (Result, error) {
				if mode == "publisher" || mode == "publisher-cleanup" {
					err := error(&policy.Reservation{URL: "https://example.com/api", Source: "header"})
					if mode == "publisher-cleanup" {
						err = errors.Join(err, errCleanupUnproved)
					}
					return Result{}, err
				}
				first, _ := api.Decode([]byte(`{"total":1,"jobs":[{"id":"1","title":"Engineer"}]}`))
				if err := task.APIReplay.converse(ctx, func(context.Context, api.Request) (*api.Document, error) { return first, nil }, false); err != nil {
					return Result{}, err
				}
				if mode == "cleanup" {
					return Result{}, errCleanupUnproved
				}
				cleaned = true
				return Result{apiReplaySessionSettled: true}, nil
			}}
			response, err := execution.executeAPIReplay(context.Background(), request)
			if err != nil || !response.Valid() {
				t.Fatal("service response invalid", err)
			}
			if mode == "success" {
				if !cleaned || response.Outcome != "success" || len(response.Inventory) == 0 {
					t.Fatal("inventory published without cleanup")
				}
			} else if len(response.Inventory) != 0 || mode == "publisher" && response.Outcome != "publisher_reserved" || mode != "publisher" && response.Outcome != "failed" {
				t.Fatal("failed cleanup published provisional inventory/policy", response.Outcome)
			}
		})
	}
}
