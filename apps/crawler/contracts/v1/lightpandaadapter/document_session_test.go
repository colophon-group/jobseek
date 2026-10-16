package lightpandaadapter

import (
	"bytes"
	"context"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"testing"
	"time"
)

func TestLargeDocumentSessionKeepsDefaultAndExplicitHTMLBounds(t *testing.T) {
	for _, tc := range []struct {
		name    string
		large   bool
		size    int
		success bool
	}{
		{"legacy", false, 2_000_001, false},
		{"large", true, 2_800_000, true},
		{"boundary", true, actions.LargeDocumentHTMLLimit, true},
		{"oversized", true, actions.LargeDocumentHTMLLimit + 1, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				raw := successfulRaw()
				raw.Evaluations = nil
				raw.HTML = bytes.Repeat([]byte("x"), tc.size)
				return NewRunnerSuccess(bound, raw)
			}}
			var adapter *Adapter
			var err error
			if tc.large {
				adapter, err = NewLargeDocumentSessionRenderOnly(runner, nil, nil)
			} else {
				adapter, err = NewDocumentSessionRenderOnly(runner, []actions.Action{{Kind: "wait", TimeoutMS: 1}}, nil)
			}
			if err != nil {
				t.Fatal(err)
			}
			result := adapter.Execute(context.Background(), retryInput())
			if (result.GetSuccess() != nil) != tc.success || runner.calls != 1 {
				t.Fatal("document budget or runner ownership changed")
			}
			if tc.success && result.GetSuccess().Html.TotalSizeBytes != uint64(tc.size) {
				t.Fatal("large document was truncated")
			}
		})
	}
}

func TestDocumentSessionBudgetSurvivesNavigationAndHonorsParent(t *testing.T) {
	pipeline := []actions.Action{{Kind: "wait", Milliseconds: 10000, TimeoutMS: 10000}}
	cards := &actions.InlineCards{ControlSelector: "button", ContentSelector: "article", IdentitySelector: "label", IdentityAttribute: "for", IdentityPattern: "(.*)", Maximum: 5, TimeoutMS: 300000}
	for _, parentBudget := range []time.Duration{0, time.Second} {
		input := retryInput()
		runner := &fakeRunner{run: func(ctx context.Context, b BoundInput) RunnerOutcome {
			deadline, ok := ctx.Deadline()
			if !ok {
				t.Fatal("unbounded session")
			}
			expected := NavigationExecutionBudget(input.Plan.Navigation) + 310*time.Second
			if parentBudget > 0 {
				expected = parentBudget
			}
			if remaining := time.Until(deadline); remaining > expected || remaining < expected-time.Second {
				t.Fatalf("session deadline %s, want %s", remaining, expected)
			}
			return successfulOutcome(b)
		}}
		adapter, err := NewDocumentSessionRenderOnly(runner, pipeline, cards)
		if err != nil {
			t.Fatal(err)
		}
		ctx := context.Background()
		if parentBudget > 0 {
			var cancel context.CancelFunc
			ctx, cancel = context.WithTimeout(ctx, parentBudget)
			defer cancel()
		}
		adapter.Execute(ctx, input)
		if runner.calls != 1 {
			t.Fatal("validated session did not reach runner")
		}
	}
}

func TestDocumentSessionRefusesInvalidBudgetBeforeRunner(t *testing.T) {
	runner := &fakeRunner{}
	if _, err := NewDocumentSessionRenderOnly(runner, nil, nil); err == nil {
		t.Fatal("empty session admitted")
	}
	if _, err := NewDocumentSessionRenderOnly(runner, []actions.Action{{Kind: "wait", TimeoutMS: 120001}}, nil); err == nil {
		t.Fatal("oversized action admitted")
	}
	cards := &actions.InlineCards{ControlSelector: "button", ContentSelector: "article", IdentitySelector: "label", IdentityAttribute: "for", IdentityPattern: "(.*)", Maximum: 5, TimeoutMS: 300001}
	if _, err := NewDocumentSessionRenderOnly(runner, nil, cards); err == nil {
		t.Fatal("oversized collection admitted")
	}
	if runner.calls != 0 {
		t.Fatal("invalid session reached runner")
	}
}
