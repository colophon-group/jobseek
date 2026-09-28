package lightpandaadapter

import (
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func retryInput() *runtimev1.BrowserExecutionInput {
	input := validInput()
	input.Plan.Evaluations = nil
	input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}
	input.Plan.Navigation.TransportRetries = 1
	parent := input.Plan.Navigation.OriginRequestId
	input.Plan.OriginOperations = append(input.Plan.OriginOperations, &runtimev1.OriginOperationRef{OriginRequestId: parent + ":transport-retry-1", OperationSequence: 2, Role: "transport_retry", ParentOriginRequestId: &parent, RequestFingerprint: input.Plan.OriginOperations[0].RequestFingerprint})
	return input
}

func TestRetryNeedsExplicitMatchingOriginAuthorityAndCannotExtendLegacy(t *testing.T) {
	input := retryInput()
	if _, _, valid := bindNavigation(input, false); valid {
		t.Fatal("legacy adapter admitted retry")
	}
	if _, _, valid := bindNavigation(input, true); !valid {
		t.Fatal("explicit render retry rejected")
	}
	for _, mutate := range []func(*runtimev1.BrowserExecutionInput){
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Navigation.TransportRetries = 2 },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.Navigation.TransportRetries = 0 },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations = i.Plan.OriginOperations[:1] },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations[1].OriginRequestId = "other" },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations[1].ParentOriginRequestId = nil },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations[1].OperationSequence = 3 },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations[1].Role = "navigation" },
		func(i *runtimev1.BrowserExecutionInput) { i.Plan.OriginOperations[1].RequestFingerprint = "other" },
	} {
		i := retryInput()
		mutate(i)
		if _, valid := ValidatedRenderExecutionBudget(i); valid {
			t.Fatal("unbound retry received extra execution authority")
		}
	}
	input.Plan.Navigation.TimeoutMs = 120000
	input.Plan.Navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 5000}
	budget, valid := ValidatedRenderExecutionBudget(input)
	if !valid || budget != 250500*time.Millisecond {
		t.Fatalf("incorrect fixed retry budget: %v %v", budget, valid)
	}
}
