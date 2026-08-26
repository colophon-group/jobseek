package adjacentpolicy

import (
	"bytes"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

var requiredControlCaseIDsForTest = map[string]bool{
	"accept_complete":                           true,
	"accept_deduplicated_bound_resume":          true,
	"accept_identical_unacked_replay":           true,
	"accept_resume_after_ambiguous_dispatch":    true,
	"reject_actual_limit_exceeded":              true,
	"reject_deadline_expired_history":           true,
	"reject_divergent_sequence_reuse":           true,
	"reject_duplicate_logical_dedup":            true,
	"reject_duplicate_origin_dispatch":          true,
	"reject_fault_metadata_mismatch":            true,
	"reject_frame_after_terminal":               true,
	"reject_late_frame_after_cancel":            true,
	"reject_manifest_revision_changed":          true,
	"reject_origin_dispatch_before_declaration": true,
	"reject_origin_identity_reused":             true,
	"reject_origin_redeclaration_changed":       true,
	"reject_request_binding_changed":            true,
	"reject_reused_attempt":                     true,
	"reject_sequence_gap":                       true,
	"reject_sequence_rewind":                    true,
	"reject_stale_fence":                        true,
	"reject_terminal_count_mismatch":            true,
	"reject_terminal_duplicate":                 true,
	"reject_terminal_missing":                   true,
	"reject_trace_binding_changed":              true,
	"reject_unknown_checkpoint":                 true,
	"reject_unknown_origin_contact":             true,
}

func controlResultByID(t *testing.T, results []controlResult, caseID string) controlResult {
	t.Helper()
	for _, result := range results {
		if result.CaseID == caseID {
			return result
		}
	}
	t.Fatalf("missing control result %q", caseID)
	return controlResult{}
}

func TestControlCorpusMatchesEveryExpectedStableResult(t *testing.T) {
	results, err := validateControlCorpus(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(results) < 50 {
		t.Fatalf("control corpus unexpectedly small: %d", len(results))
	}
	seen := map[string]bool{}
	accepted := 0
	for _, result := range results {
		if seen[result.CaseID] {
			t.Fatalf("duplicate result ID: %s", result.CaseID)
		}
		seen[result.CaseID] = true
		if result.Accepted {
			accepted++
			if result.Code != "ok" || result.BindingSHA256 == "" || result.Terminal == nil {
				t.Fatalf("accepted case lacks complete result: %#v", result)
			}
		}
	}
	if accepted < 15 {
		t.Fatalf("disconnect/replay acceptance matrix shrank: %d", accepted)
	}
	for caseID := range mandatoryControlCaseIDs {
		if !seen[caseID] {
			t.Fatalf("mandatory case disappeared: %s", caseID)
		}
	}
}

func TestControlRequiredIDsAndErrorRegistryCannotDriftBehindTheCorpus(t *testing.T) {
	if !reflect.DeepEqual(mandatoryControlCaseIDs, requiredControlCaseIDsForTest) {
		t.Fatalf("required control case registry drifted: %#v", mandatoryControlCaseIDs)
	}
	corpus, err := loadControlCorpus(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	exercised := map[string]bool{}
	for _, item := range corpus.Cases {
		exercised[item.Expected.Code] = true
	}
	if !reflect.DeepEqual(exercised, controlErrorCodes) {
		t.Fatalf("every stable local error must be exercised\nregistry=%#v\nexercised=%#v", controlErrorCodes, exercised)
	}
}

func TestControlPythonAndGoResultsMatchExactly(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("python3 is required for cross-language control conformance")
	}
	root := contractRoot(t)
	command := exec.Command( // #nosec G204 -- executable and repository paths are fixed.
		python,
		filepath.Join(root, "tools", "check_protocol.py"),
		"--root",
		root,
		"--json",
	)
	command.Env = append(
		os.Environ(),
		"GOPROXY=off",
		"GOSUMDB=off",
		"NO_PROXY=*",
		"no_proxy=*",
	)
	output, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("Python control validator failed: %v\n%s", err, output)
	}
	var pythonResults []controlResult
	if err := json.Unmarshal(output, &pythonResults); err != nil {
		t.Fatalf("Python control output is not the stable result schema: %v\n%s", err, output)
	}
	goResults, err := validateControlCorpus(root)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(goResults, pythonResults) {
		goJSON, _ := json.Marshal(goResults)
		t.Fatalf("Python/Go control results differ\nGo: %s\nPython: %s", goJSON, output)
	}
}

func TestControlCorpusGenerationIsDeterministic(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("python3 is required for corpus determinism")
	}
	command := exec.Command( // #nosec G204 -- executable and repository path are fixed.
		python,
		filepath.Join(contractRoot(t), "fixtures", "control", "generate.py"),
		"--check",
	)
	command.Env = append(os.Environ(), "NO_PROXY=*", "no_proxy=*")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("control fixture drift: %v\n%s", err, output)
	}
}

func TestControlReplayCountsLogicalStateOnceAndPhysicalCreditEveryTime(t *testing.T) {
	results, err := validateControlCorpus(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	deduplicated := controlResultByID(t, results, "accept_deduplicated_bound_resume")
	if deduplicated.Counts.ReplayedFrames != 1 || deduplicated.Counts.Deduplicated != 1 || deduplicated.Counts.Dispatched != 1 {
		t.Fatalf("ambiguous replay/dedup was double-counted: %#v", deduplicated.Counts)
	}
	if deduplicated.Terminal == nil || deduplicated.Terminal.FrameCount != deduplicated.Counts.Frames {
		t.Fatalf("terminal frame_count is not unique nonterminal logical frames: %#v", deduplicated)
	}
	dynamic := controlResultByID(t, results, "accept_unacked_dynamic_declaration_replay")
	if dynamic.Counts.Declared != 2 || dynamic.Counts.ReplayedFrames != 1 || len(dynamic.Ledger) != 2 {
		t.Fatalf("dynamic declaration replay changed the durable ledger: %#v", dynamic)
	}
	credit := controlResultByID(t, results, "reject_physical_replay_credit_exhausted")
	if credit.Code != "credit_exceeded" || credit.Counts.ReplayedFrames != 1 {
		t.Fatalf("physical replay did not consume credit: %#v", credit)
	}
	if replenish := controlResultByID(t, results, "accept_window_replenishment"); !replenish.Accepted {
		t.Fatalf("bounded window replenishment was rejected: %#v", replenish)
	}
	if reused := controlResultByID(t, results, "reject_artifact_identity_reused"); reused.Code != "artifact_identity_reused" {
		t.Fatalf("logical artifact identity reuse was not rejected: %#v", reused)
	}
	if origins := controlResultByID(t, results, "reject_origin_local_cap_exceeded"); origins.Code != "origin_local_cap_exceeded" {
		t.Fatalf("local origin safety cap was not enforced: %#v", origins)
	}
	if errors := controlResultByID(t, results, "reject_error_local_cap_exceeded"); errors.Code != "error_local_cap_exceeded" {
		t.Fatalf("local error safety cap was not enforced: %#v", errors)
	}
	if partial := controlResultByID(t, results, "accept_partial_replay_fault_resume"); !partial.Accepted {
		t.Fatalf("fault after a physical partial replay was rejected: %#v", partial)
	}
}

func TestControlResumeCancelAndFixtureHistoryInvariants(t *testing.T) {
	results, err := validateControlCorpus(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	checks := map[string]string{
		"reject_resume_checkpoint_rewind":           "sequence_rewind",
		"reject_resume_without_reconnect_handshake": "resume_handshake_missing",
		"reject_cancelled_terminal_without_cancel":  "terminal_count_mismatch",
		"reject_fixture_cut_mismatch":               "fixture_cut_mismatch",
		"reject_fixture_injection_phase_mismatch":   "fixture_injection_phase_mismatch",
	}
	for caseID, code := range checks {
		if result := controlResultByID(t, results, caseID); result.Code != code {
			t.Fatalf("%s: got %#v", caseID, result)
		}
	}
	if cancelled := controlResultByID(t, results, "accept_cancelled_terminal_after_cancel"); !cancelled.Accepted {
		t.Fatalf("bound cancelled terminal was rejected: %#v", cancelled)
	}
	deadline := controlResultByID(t, results, "reject_deadline_after_durable_prefix")
	if deadline.Code != "deadline_exceeded" || deadline.Counts.Frames != 1 {
		t.Fatalf("durable prefix was not restored before deadline history: %#v", deadline)
	}
}

func TestControlEveryResumeRequiresFreshHelloPair(t *testing.T) {
	corpus, err := loadControlCorpus(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range corpus.Cases {
		for index, event := range item.Events {
			if event.Resume == nil {
				continue
			}
			if item.ID == "reject_resume_without_reconnect_handshake" {
				if index > 0 && item.Events[index-1].ServerHello != nil {
					t.Fatal("missing-handshake negative unexpectedly has ServerHello")
				}
				continue
			}
			if index < 2 || item.Events[index-2].ClientHello == nil || item.Events[index-1].ServerHello == nil {
				t.Fatalf("%s: resume lacks fresh Hello/ServerHello", item.ID)
			}
		}
	}
}

func TestControlManifestDoesNotInventProtocolEventOrLimitFields(t *testing.T) {
	content, err := os.ReadFile(filepath.Join(contractRoot(t), "fixtures", "control", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range [][]byte{
		[]byte(`"observed_at_rfc3339"`),
		[]byte(`"max_origin_operations"`),
		[]byte(`"max_errors"`),
		[]byte(`"error_count"`),
		[]byte(`"durable_checkpoint"`),
		[]byte(`"candidate_start"`),
	} {
		if bytes.Contains(content, forbidden) {
			t.Fatalf("control corpus contains invented field %s", forbidden)
		}
	}
}

func TestControlStateImplementationCannotImportNetworkPackages(t *testing.T) {
	path := filepath.Join(contractRoot(t), "conformance", "go", "state.go")
	parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range parsed.Decls {
		declaration, ok := item.(*ast.GenDecl)
		if !ok {
			continue
		}
		for _, spec := range declaration.Specs {
			importSpec, ok := spec.(*ast.ImportSpec)
			if !ok {
				continue
			}
			name, err := strconv.Unquote(importSpec.Path.Value)
			if err != nil {
				t.Fatal(err)
			}
			if name == "net" || strings.HasPrefix(name, "net/") {
				t.Fatalf("control validator must remain offline, imported %q", name)
			}
		}
	}
}

func TestControlStrictDecoderRejectsUnknownWireFields(t *testing.T) {
	content := []byte(`{"direction":"client","unknown":true}`)
	var event controlEvent
	if err := decodeStrict(content, &event); err == nil {
		t.Fatal("unknown fixture field was accepted")
	}
}

func TestControlStrictDecoderRejectsBooleanTerminalCounters(t *testing.T) {
	content := []byte(`{"type":"terminal","frame_count":true}`)
	var payload payloadShape
	if err := decodeStrict(content, &payload); err == nil {
		t.Fatal("boolean terminal counter was accepted as an integer")
	}
}
