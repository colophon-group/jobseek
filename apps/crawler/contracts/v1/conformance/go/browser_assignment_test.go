package adjacentpolicy

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

var browserBoundaryRequiredCaseIDs = []string{
	"accept_chromium_navigation_success",
	"accept_chromium_success",
	"accept_lightpanda_interaction_success",
	"accept_lightpanda_success",
	"accept_typed_provider_error",
	"accept_typed_unsupported_preflight",
	"reject_assignment_changed",
	"reject_capability_class_mismatch",
	"reject_duplicate_plan_capability",
	"reject_duplicate_provider_capability",
	"reject_error_partial_output",
	"reject_fallback_chromium_to_lightpanda",
	"reject_fallback_lightpanda_to_chromium",
	"reject_invalid_routing_revision",
	"reject_missing_provider_invocation",
	"reject_null_assignment",
	"reject_null_assignment_backend",
	"reject_null_assignment_capability_class",
	"reject_null_assignment_routing_revision",
	"reject_null_assignment_service_lane",
	"reject_null_authoritative_partial_output",
	"reject_null_origin_before_assignment",
	"reject_null_origin_operations",
	"reject_null_plan_capabilities",
	"reject_null_provider_capabilities",
	"reject_null_provider_invocations",
	"reject_null_result",
	"reject_null_result_backend",
	"reject_null_result_outcome",
	"reject_null_unsupported_capabilities",
	"reject_origin_before_assignment",
	"reject_oversized_routing_revision",
	"reject_provider_backend_mismatch",
	"reject_result_backend_mismatch",
	"reject_retry_same_backend",
	"reject_service_lane_mismatch",
	"reject_success_missing_origin",
	"reject_success_with_error",
	"reject_success_with_unsupported",
	"reject_unknown_assignment_backend",
	"reject_unknown_capability_class",
	"reject_unknown_input_field",
	"reject_unknown_plan_capability",
	"reject_unknown_provider_capability",
	"reject_unknown_result_outcome",
	"reject_unknown_service_lane",
	"reject_unspecified_assignment_backend",
	"reject_unspecified_capability_class",
	"reject_unspecified_plan_capability",
	"reject_unspecified_service_lane",
	"reject_untyped_error",
	"reject_unexpected_unsupported",
	"reject_unsupported_extra_capability",
	"reject_unsupported_missing_capability",
	"reject_unsupported_origin_operation",
	"reject_unsupported_partial_output",
	"reject_unsupported_provider_invocation",
}

type browserBoundaryCase struct {
	Expected browserBoundaryDecision `json:"expected"`
	ID       string                  `json:"id"`
	Input    map[string]any          `json:"input"`
}

type browserBoundaryManifest struct {
	Cases           []browserBoundaryCase `json:"cases"`
	Defaults        map[string]any        `json:"defaults"`
	Format          string                `json:"format"`
	RequiredCaseIDs []string              `json:"required_case_ids"`
}

func loadBrowserBoundaryManifest(t *testing.T) (browserBoundaryManifest, []byte) {
	t.Helper()
	root := filepath.Join(contractRoot(t), "fixtures", "browser_executor")
	content, err := os.ReadFile(filepath.Join(root, "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var document any
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&document); err != nil {
		t.Fatal(err)
	}
	canonical, err := json.MarshalIndent(document, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	canonical = append(canonical, '\n')
	if !bytes.Equal(content, canonical) {
		t.Fatal("browser boundary manifest is not canonical pretty JSON")
	}
	digest := sha256.Sum256(content)
	expectedDigest, err := os.ReadFile(filepath.Join(root, "manifest.sha256"))
	if err != nil {
		t.Fatal(err)
	}
	actualDigest := hex.EncodeToString(digest[:]) + "  manifest.json\n"
	if string(expectedDigest) != actualDigest {
		t.Fatalf("browser boundary digest = %q, want %q", expectedDigest, actualDigest)
	}
	var manifest browserBoundaryManifest
	if err := json.Unmarshal(content, &manifest); err != nil {
		t.Fatal(err)
	}
	return manifest, content
}

func TestBrowserBoundaryRequiredIDsAreHardCodedAndComplete(t *testing.T) {
	manifest, _ := loadBrowserBoundaryManifest(t)
	if manifest.Format != browserBoundaryFormat {
		t.Fatalf("browser boundary format = %q", manifest.Format)
	}
	if !reflect.DeepEqual(manifest.RequiredCaseIDs, browserBoundaryRequiredCaseIDs) {
		t.Fatalf("required IDs differ\n got: %v\nwant: %v", manifest.RequiredCaseIDs, browserBoundaryRequiredCaseIDs)
	}
	if len(manifest.Cases) != len(browserBoundaryRequiredCaseIDs) {
		t.Fatalf("case count = %d, want %d", len(manifest.Cases), len(browserBoundaryRequiredCaseIDs))
	}
	seen := make(map[string]bool, len(manifest.Cases))
	for index, item := range manifest.Cases {
		if item.ID != browserBoundaryRequiredCaseIDs[index] || seen[item.ID] {
			t.Fatalf("invalid case ID/order at %d: %q", index, item.ID)
		}
		seen[item.ID] = true
	}
}

func TestBrowserBoundaryEveryCaseMatchesExactDecision(t *testing.T) {
	manifest, _ := loadBrowserBoundaryManifest(t)
	accepted := 0
	unsupported := 0
	for _, item := range manifest.Cases {
		item := item
		t.Run(item.ID, func(t *testing.T) {
			merged := browserDeepMerge(manifest.Defaults, item.Input)
			input, ok := merged.(map[string]any)
			if !ok {
				t.Fatal("merged browser input is not an object")
			}
			actual := evaluateBrowserBoundary(input)
			if actual != item.Expected {
				t.Fatalf("decision = %+v, want %+v", actual, item.Expected)
			}
			switch actual.Status {
			case "accepted":
				accepted++
			case "unsupported":
				unsupported++
			case "rejected":
			default:
				t.Fatalf("unknown status %q", actual.Status)
			}
		})
	}
	if accepted != 5 || unsupported != 1 {
		t.Fatalf("accepted/unsupported counts = %d/%d, want 5/1", accepted, unsupported)
	}
}
