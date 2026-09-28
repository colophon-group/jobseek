package lightpandaadapter

import (
	"context"
	"testing"
	"time"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func navigationInput() *runtimev1.BrowserExecutionInput {
	input := validInput()
	input.Plan.Evaluations = nil
	input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER}
	return input
}

func TestNavigationAdapterWaitsAndFallbackBoundBeforeRunner(t *testing.T) {
	for _, wait := range []runtimev1.WaitCondition{1, 2, 3, 4} {
		runner := &fakeRunner{run: func(ctx context.Context, bound BoundInput) RunnerOutcome {
			deadline, _ := ctx.Deadline()
			if time.Until(deadline) < 30*time.Second {
				t.Fatal("fallback deadline was omitted")
			}
			status := uint32(200)
			return NewRunnerSuccess(bound, &RawSuccess{FinalURL: "https://example.test/jobs", Status: &status, HTML: []byte("<h1>Engineer</h1>")})
		}}
		adapter, err := NewNavigationRenderOnly(runner)
		if err != nil {
			t.Fatal(err)
		}
		input := navigationInput()
		input.Plan.Navigation.WaitUntil = wait
		fallbackWait := runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED
		if wait == fallbackWait {
			fallbackWait = runtimev1.WaitCondition_WAIT_CONDITION_LOAD
		}
		input.Plan.Navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: fallbackWait, TimeoutMs: 5000}
		if result := adapter.Execute(context.Background(), input); result.GetSuccess() == nil || runner.calls != 1 {
			t.Fatalf("valid wait rejected: %v", result)
		}
	}
	for _, mutate := range []func(*runtimev1.NavigationPlan){
		func(n *runtimev1.NavigationPlan) { n.WaitUntil = 0 },
		func(n *runtimev1.NavigationPlan) { n.WaitUntil = 99 },
		func(n *runtimev1.NavigationPlan) {
			n.Fallback = &runtimev1.NavigationFallback{WaitUntil: 0, TimeoutMs: 1000}
		},
		func(n *runtimev1.NavigationPlan) {
			n.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 0}
		},
		func(n *runtimev1.NavigationPlan) {
			n.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 5001}
		},
		func(n *runtimev1.NavigationPlan) {
			n.Fallback = &runtimev1.NavigationFallback{WaitUntil: 3, TimeoutMs: 1000}
		},
		func(n *runtimev1.NavigationPlan) {
			n.TimeoutMs = 500
			n.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 501}
		},
	} {
		runner := &fakeRunner{run: func(context.Context, BoundInput) RunnerOutcome {
			t.Fatal("invalid navigation reached runner")
			return RunnerOutcome{}
		}}
		adapter, _ := NewNavigationRenderOnly(runner)
		input := navigationInput()
		mutate(input.Plan.Navigation)
		if result := adapter.Execute(context.Background(), input); result.GetError() == nil || runner.calls != 0 {
			t.Fatal("invalid navigation admitted")
		}
	}
}

func TestOriginalAdapterStillRejectsNewNavigationBehavior(t *testing.T) {
	runner := &fakeRunner{run: func(context.Context, BoundInput) RunnerOutcome {
		t.Fatal("legacy adapter accepted new wait")
		return RunnerOutcome{}
	}}
	adapter, _ := NewRenderOnly(runner)
	input := navigationInput()
	input.Plan.Navigation.WaitUntil = 2
	if adapter.Execute(context.Background(), input).GetError() == nil {
		t.Fatal("legacy wait contract changed")
	}
	input.Plan.Navigation.WaitUntil = 3
	input.Plan.Navigation.Fallback = &runtimev1.NavigationFallback{WaitUntil: 2, TimeoutMs: 1000}
	if adapter.Execute(context.Background(), input).GetError() == nil {
		t.Fatal("legacy fallback contract changed")
	}
}
