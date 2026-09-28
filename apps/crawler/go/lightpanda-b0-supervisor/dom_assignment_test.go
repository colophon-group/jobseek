package main

import (
	"encoding/json"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"testing"
)

func TestDOMProducerPreservesNavigationAndFrozenParserIdentity(t *testing.T) {
	for _, wait := range []string{"commit", "domcontentloaded", "load", "networkidle"} {
		for _, fallback := range []any{nil, "domcontentloaded", "load"} {
			config := map[string]any{
				"browser_backend": "lightpanda", "routing_revision": "dom-b0-1", "render": true,
				"timeout": 30000, "wait": wait, "wait_fallback": fallback,
				"steps":    []any{map[string]any{"tag": "h1", "field": "title"}},
				"defaults": map[string]any{"raw_metadata": "kept"},
			}
			raw, err := json.Marshal(map[string]any{"scraper_type": "dom", "scraper_config": config})
			if err != nil {
				t.Fatal(err)
			}
			parser, identity, err := producerAssignment(string(raw))
			if err != nil {
				t.Fatal(err)
			}
			route := routeIdentity{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: engineOwner}
			task, err := buildProducerTask(validProducerRequest(), route, parser, identity, 1)
			if err != nil {
				t.Fatal(err)
			}
			if task.Envelope.ScraperType != "dom" || task.Envelope.Wait != wait {
				t.Fatal("DOM identity lost")
			}
			input, err := browserInput(task)
			if err != nil {
				t.Fatal(err)
			}
			wantsFallback := fallback != nil && fallback != wait
			if (input.Plan.Navigation.Fallback != nil) != wantsFallback {
				t.Fatal("fallback presence differs from current-document navigation")
			}
			if wantsFallback && input.Plan.Navigation.Fallback.TimeoutMs != 5000 {
				t.Fatal("fallback not bounded")
			}
			if input.Plan.Navigation.WaitUntil == runtimev1.WaitCondition_WAIT_CONDITION_UNSPECIFIED || len(input.Plan.OriginOperations) != 2 || input.Plan.Navigation.TransportRetries != 1 {
				t.Fatal("navigation changed operation cardinality")
			}
			task.Envelope.Wait = "invalid"
			if validateParserConfig(task.Envelope) == nil {
				t.Fatal("envelope/config readiness drift accepted")
			}
		}
	}
}
