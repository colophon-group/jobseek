package lightpandaadapter

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

var requiredCorpusCaseIDs = []string{
	"accept_render_only",
	"accept_render_evaluate",
	"reject_assignment_before_unsupported",
	"reject_duplicate_capability",
	"reject_missing_render",
	"reject_evaluate_without_plan",
	"reject_plan_without_evaluate",
	"reject_non_load_wait",
	"reject_adjacent_action",
	"unsupported_features_are_sorted",
}

type corpusExpected struct {
	Capabilities []string `json:"capabilities"`
	Code         string   `json:"code"`
	Status       string   `json:"status"`
}

type corpusCase struct {
	Expected corpusExpected `json:"expected"`
	ID       string         `json:"id"`
	Mutation string         `json:"mutation"`
}

type adapterCorpus struct {
	Cases           []corpusCase `json:"cases"`
	Format          string       `json:"format"`
	RequiredCaseIDs []string     `json:"required_case_ids"`
}

func loadAdapterCorpus(t *testing.T) adapterCorpus {
	t.Helper()
	root := filepath.Join("..", "fixtures", "lightpanda_adapter")
	content, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var generic any
	if err := json.Unmarshal(content, &generic); err != nil {
		t.Fatal(err)
	}
	canonical, err := json.MarshalIndent(generic, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	canonical = append(canonical, '\n')
	if !bytes.Equal(content, canonical) {
		t.Fatal("adapter corpus is not canonical pretty JSON")
	}
	digest := sha256.Sum256(content)
	digestFile, err := os.ReadFile(filepath.Join(root, "manifest.sha256"))
	if err != nil {
		t.Fatal(err)
	}
	wantDigest := hex.EncodeToString(digest[:]) + "  manifest.json\n"
	if string(digestFile) != wantDigest {
		t.Fatalf("manifest digest = %q, want %q", digestFile, wantDigest)
	}
	var corpus adapterCorpus
	if err := json.Unmarshal(content, &corpus); err != nil {
		t.Fatal(err)
	}
	return corpus
}

func TestAdapterCorpusIsClosedAndMatchesImplementation(t *testing.T) {
	corpus := loadAdapterCorpus(t)
	if corpus.Format != "jobseek.lightpanda-adapter-b1/v1" ||
		!reflect.DeepEqual(corpus.RequiredCaseIDs, requiredCorpusCaseIDs) ||
		len(corpus.Cases) != len(requiredCorpusCaseIDs) {
		t.Fatalf("invalid corpus header: %#v", corpus)
	}
	for index, testCase := range corpus.Cases {
		if testCase.ID != requiredCorpusCaseIDs[index] {
			t.Fatalf("case %d = %q, want %q", index, testCase.ID, requiredCorpusCaseIDs[index])
		}
		t.Run(testCase.ID, func(t *testing.T) {
			input := validInput()
			applyCorpusMutation(t, input, testCase.Mutation)
			runner := &fakeRunner{run: func(_ context.Context, bound BoundInput) RunnerOutcome {
				raw := successfulRaw()
				if len(bound.Input().Plan.Evaluations) == 0 {
					raw.Evaluations = nil
				}
				return NewRunnerSuccess(bound, raw)
			}}
			privacy := &fakePrivacy{}
			adapter, _ := New(runner, privacy)
			result := adapter.Execute(context.Background(), input)
			actual := corpusDecision(result)
			if !reflect.DeepEqual(actual, testCase.Expected) {
				t.Fatalf("decision = %#v, want %#v", actual, testCase.Expected)
			}
			if testCase.Expected.Status != "success" && runner.calls != 0 {
				t.Fatalf("preflight case invoked runner %d times", runner.calls)
			}
		})
	}
}

func applyCorpusMutation(t *testing.T, input *runtimev1.BrowserExecutionInput, mutation string) {
	t.Helper()
	switch mutation {
	case "none":
	case "render_only":
		input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
		}
		input.Plan.Evaluations = nil
	case "wrong_backend_with_frames":
		input.Assignment.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM
		input.Plan.RequiredCapabilities = append(
			input.Plan.RequiredCapabilities,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
		)
	case "duplicate_render":
		input.Plan.RequiredCapabilities = append(
			input.Plan.RequiredCapabilities,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
		)
	case "evaluate_only":
		input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_EVALUATE,
		}
	case "remove_evaluation":
		input.Plan.Evaluations = nil
	case "remove_evaluate_capability":
		input.Plan.RequiredCapabilities = []runtimev1.BrowserCapability{
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_RENDER,
		}
	case "dom_content_loaded":
		input.Plan.Navigation.WaitUntil = runtimev1.WaitCondition_WAIT_CONDITION_DOM_CONTENT_LOADED
	case "add_action":
		input.Plan.Actions = []*runtimev1.BrowserAction{{ActionId: "action-1"}}
	case "add_unsorted_unsupported":
		input.Plan.RequiredCapabilities = append(
			input.Plan.RequiredCapabilities,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS,
			runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES,
		)
	default:
		t.Fatalf("unknown corpus mutation %q", mutation)
	}
}

func corpusDecision(result *runtimev1.BrowserResult) corpusExpected {
	if result.GetSuccess() != nil {
		return corpusExpected{Status: "success"}
	}
	if unsupported := result.GetUnsupported(); unsupported != nil {
		names := make([]string, 0, len(unsupported.Capabilities))
		for _, capability := range unsupported.Capabilities {
			switch capability {
			case runtimev1.BrowserCapability_BROWSER_CAPABILITY_ACTIONS:
				names = append(names, "actions")
			case runtimev1.BrowserCapability_BROWSER_CAPABILITY_FRAMES:
				names = append(names, "frames")
			case runtimev1.BrowserCapability_BROWSER_CAPABILITY_PROXY:
				names = append(names, "proxy")
			default:
				names = append(names, "unexpected")
			}
		}
		return corpusExpected{Status: "unsupported", Capabilities: names}
	}
	if failure := result.GetError(); failure != nil && failure.Error != nil &&
		failure.Error.Code == runtimev1.ErrorCode_ERROR_CODE_INVALID_CONFIG {
		return corpusExpected{Status: "error", Code: "invalid_config"}
	}
	return corpusExpected{Status: "error", Code: "unexpected"}
}
