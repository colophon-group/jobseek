//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	replay "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/apireplay"
	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestAPIReplayClientServerPreservesLargeCompleteInventory(t *testing.T) {
	for _, mode := range []string{"complete", "over-bound"} {
		t.Run(mode, func(t *testing.T) {
			size := 9 << 20
			if mode == "over-bound" {
				size = replay.ResponseLimit
			}
			description := strings.Repeat("x", size)
			body, err := json.Marshal(map[string]any{"jobs": []any{map[string]any{"id": "1", "title": "Engineer", "body": description}}})
			if err != nil {
				t.Fatal(err)
			}
			document, err := api.Decode(body)
			if err != nil {
				t.Fatal(err)
			}
			fixture := newServiceTLSFixture(t)
			runner := func(ctx context.Context, _ Config, task Task) (Result, error) {
				if err := task.APIReplay.converse(ctx, func(context.Context, api.Request) (*api.Document, error) { return document, nil }, false); err != nil {
					return Result{}, err
				}
				return Result{apiReplaySessionSettled: true}, nil
			}
			execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary, EgressPolicy: fixture.server.serviceEgressPolicy.egressPolicy}, runner)
			if err != nil {
				t.Fatal(err)
			}
			_, address, stop := startRuntimeV1ServiceTest(t, fixture, execution)
			defer stop()
			client := dayforceClientFixture(t, fixture, address)
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer cancel()
			held, err := client.Reserve(ctx)
			if err != nil {
				t.Fatal(err)
			}
			response, err := held.APIReplay(ctx, replay.Request{Protocol: replay.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), BoardURL: "https://example.com/careers", Metadata: json.RawMessage(`{"browser":true,"api_url":"https://example.com/api","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title","description":"body"}}`), TimeoutMS: 20000})
			if err != nil {
				t.Fatal("bounded client/server exchange failed", err)
			}
			if mode != "complete" {
				if response.Outcome != "failed" || len(response.Inventory) != 0 {
					t.Fatal("oversized or unclean result published partial inventory")
				}
				return
			}
			var inventory api.Inventory
			if response.Outcome != "success" || json.Unmarshal(response.Inventory, &inventory) != nil || inventory.Truncated || len(inventory.Jobs) != 1 || inventory.Jobs[0].Description != description {
				t.Fatal("complete large inventory or fields lost across framing")
			}
		})
	}
}
