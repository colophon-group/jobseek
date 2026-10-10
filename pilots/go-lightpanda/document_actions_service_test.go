//go:build !densitybench

package main

import (
	"context"
	"encoding/json"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	lp "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaclient"
	"google.golang.org/protobuf/proto"
	"strings"
	"testing"
	"time"
)

func TestDocumentActionsServicePreflightCleanupAndBinding(t *testing.T) {
	for _, mode := range []string{"success", "large-document", "oversized-document", "cleanup", "evaluation", "malformed"} {
		t.Run(mode, func(t *testing.T) {
			input, err := lp.NavigationInput(lp.Navigation{URL: "https://example.com/careers", RoutingRevision: "actions-test", OriginRequestID: "test-origin", Wait: "load", TimeoutMS: 1000})
			if err != nil {
				t.Fatal(err)
			}
			if mode == "evaluation" {
				input.Plan.RequiredCapabilities = append(input.Plan.RequiredCapabilities, runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE)
				input.Plan.Evaluations = []*runtimev1.EvaluationPlan{{EvaluationId: "private", Expression: "private", MaxResultBytes: 100, NetworkEffect: runtimev1.BrowserNetworkEffect_BROWSER_NETWORK_EFFECT_NONE}}
			}
			payload, _ := proto.Marshal(input)
			if mode == "malformed" {
				payload = []byte{255}
			}
			request := actions.Request{Protocol: actions.Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: payload, Actions: []actions.Action{{Kind: "wait", TimeoutMS: 10000}}}
			calls, cleaned := 0, false
			execution, err := newRuntimeV1ServiceExecution(Config{Binary: lightpandaServiceBinary}, func(ctx context.Context, c Config, task Task) (Result, error) {
				calls++
				if len(task.Actions) != 1 || task.Actions[0].TimeoutMS != 10000 || c.TaskTimeout != 11*time.Second {
					t.Fatal("pipeline or budget lost")
				}
				if mode == "cleanup" {
					return Result{}, errCleanupUnproved
				}
				cleaned = true
				html := "<html><body>Engineer</body></html>"
				if mode == "large-document" || mode == "oversized-document" {
					html = strings.Repeat("x", 2_000_000)
					if mode == "oversized-document" {
						html += "x"
					}
				}
				return Result{Status: 200, FinalURL: task.URL, HTML: html, HTMLPresent: true}, nil
			})
			if err != nil {
				t.Fatal(err)
			}
			response, err := execution.executeDocumentActions(context.Background(), request)
			if mode == "malformed" {
				if err == nil || calls != 0 {
					t.Fatal("malformed input contacted runner")
				}
				return
			}
			if err != nil || !response.Valid() || response.RequestID != request.RequestID || response.ConfigFingerprint != request.ConfigFingerprint {
				t.Fatal("response lost source binding", err)
			}
			result := new(runtimev1.BrowserResult)
			if proto.Unmarshal(response.Result, result) != nil {
				t.Fatal("typed result invalid")
			}
			if mode == "success" || mode == "large-document" {
				if !cleaned || result.GetSuccess() == nil {
					t.Fatal("completed document withheld", "native_code", result.GetError().GetError().GetCode(), "html_limit", actions.ResponseLimit)
				}
				if mode == "large-document" {
					body, err := json.Marshal(response)
					var decoded actions.Response
					if err != nil || len(body) > actions.ResponseLimit || actions.Decode(body, actions.ResponseLimit, &decoded) != nil || !decoded.Valid() || result.GetSuccess().Html.TotalSizeBytes != 2_000_000 {
						t.Fatal("large typed document lost its bounded JSON envelope")
					}
				}
			} else {
				if result.GetSuccess() != nil || (result.GetError() == nil && result.GetUnsupported() == nil) {
					t.Fatal("partial output published")
				}
				if mode == "evaluation" && calls != 0 {
					t.Fatal("B1 evaluation was admitted")
				}
			}
		})
	}
}
