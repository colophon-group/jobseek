package lightpandaclient

import (
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func TestNavigationPreservesWaitFallbackAndOriginRetryAttribution(t *testing.T) {
	fallback := "domcontentloaded"
	n := Navigation{URL: "https://example.com/jobs/42", RoutingRevision: "fixture-v1", OriginRequestID: "ordinary-detail:fixture", Wait: "networkidle", WaitFallback: &fallback, TimeoutMS: 30000, TransportRetries: 1}
	input, err := NavigationInput(n)
	if err != nil {
		t.Fatal(err)
	}
	plan := input.Plan.Navigation
	if plan.WaitUntil != runtimev1.WaitCondition_WAIT_CONDITION_NETWORK_IDLE || plan.TimeoutMs != 30000 || plan.Fallback.WaitUntil != runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED || plan.Fallback.TimeoutMs != 5000 || plan.TransportRetries != 1 {
		t.Fatal("existing browser navigation defaults differ")
	}
	refs := input.Plan.OriginOperations
	if len(refs) != 2 || refs[0].Role != "navigation" || refs[1].Role != "transport_retry" || refs[1].GetParentOriginRequestId() != n.OriginRequestID || refs[1].RequestFingerprint != refs[0].RequestFingerprint || refs[1].OperationSequence != 2 {
		t.Fatal("origin retry attribution differs")
	}
	n.TimeoutMS = 2500
	n.TransportRetries = 0
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback.TimeoutMs != 2500 || len(input.Plan.OriginOperations) != 1 {
		t.Fatal("bounded navigation fallback differs", err)
	}
	n.WaitFallback = nil
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback != nil {
		t.Fatal("disabled fallback restored", err)
	}
	fallback = n.Wait
	n.WaitFallback = &fallback
	input, err = NavigationInput(n)
	if err != nil || input.Plan.Navigation.Fallback != nil {
		t.Fatal("identical fallback duplicated", err)
	}
}
