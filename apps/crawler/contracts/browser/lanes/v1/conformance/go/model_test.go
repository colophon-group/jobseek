package browserlanesv1

import (
	"bytes"
	"encoding/json"
	"os"
	"sort"
	"strings"
	"testing"
)

const (
	fixturePath = "../../fixtures/scenarios.json"
	sidecarPath = "../../fixtures/scenarios.sha256"
)

func TestCanonicalCorpusHasExactIndependentParity(t *testing.T) {
	raw, corpus := loadCorpus(t)
	if len(corpus.Cases) != 79 {
		t.Fatalf("case count = %d; want 79", len(corpus.Cases))
	}
	if len(raw) == 0 || raw[len(raw)-1] != '\n' || bytes.HasSuffix(raw, []byte("\n\n")) {
		t.Fatal("corpus must have exactly one final LF")
	}
	for _, testCase := range corpus.Cases {
		t.Run(testCase.ID, func(t *testing.T) {
			actual, err := RunCase(testCase)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(actual, testCase.Expected) {
				t.Fatalf("result bytes differ\nactual: %s\nexpected: %s", actual, testCase.Expected)
			}
			if digestBytes(actual) != testCase.ResultDigest {
				t.Fatalf("digest = %q; want %q", digestBytes(actual), testCase.ResultDigest)
			}
		})
	}
}

func TestCorpusCoversEveryClosedReason(t *testing.T) {
	_, corpus := loadCorpus(t)
	covered := map[string]bool{}
	for _, testCase := range corpus.Cases {
		if bytes.Equal(testCase.Expected, []byte(`{"error":"invalid_input"}`)) {
			covered["invalid_input"] = true
			continue
		}
		var result Result
		if err := json.Unmarshal(testCase.Expected, &result); err != nil {
			t.Fatal(err)
		}
		for _, lane := range laneNames {
			for _, reason := range result.Lanes[lane].Reasons {
				covered[reason] = true
			}
		}
	}
	for reason := range reasonRegistry {
		if !covered[reason] {
			t.Errorf("closed reason %q is not covered", reason)
		}
	}
	if len(covered) != 27 || len(reasonRegistry) != 27 {
		t.Fatalf("covered=%d registry=%d; want 27", len(covered), len(reasonRegistry))
	}
}

func TestAggregateSidecarBindsExactRawCorpusBytes(t *testing.T) {
	raw := mustRead(t, fixturePath)
	sidecar := mustRead(t, sidecarPath)
	if err := VerifyCorpusSidecar(raw, sidecar); err != nil {
		t.Fatal(err)
	}
	for name, candidate := range map[string][]byte{
		"changed corpus":  append(append([]byte{}, raw...), 'x'),
		"changed hash":    append([]byte{'0'}, sidecar[1:]...),
		"uppercase":       []byte(strings.ToUpper(string(sidecar))),
		"missing LF":      bytes.TrimSuffix(sidecar, []byte("\n")),
		"filename suffix": append(bytes.TrimSuffix(sidecar, []byte("\n")), []byte("  scenarios.json\n")...),
		"extra LF":        append(append([]byte{}, sidecar...), '\n'),
	} {
		t.Run(name, func(t *testing.T) {
			boundCorpus := raw
			if name == "changed corpus" {
				boundCorpus = candidate
				candidate = sidecar
			}
			if VerifyCorpusSidecar(boundCorpus, candidate) == nil {
				t.Fatal("tampered aggregate binding accepted")
			}
		})
	}
}

func TestCorpusDecoderRejectsDuplicateKeysIDsAndTampering(t *testing.T) {
	raw := mustRead(t, fixturePath)
	duplicateKey := bytes.Replace(raw, []byte(`{"cases":`), []byte(`{"cases":[],"cases":`), 1)
	duplicateID := mutateCorpus(t, raw, func(root map[string]any) {
		cases := root["cases"].([]any)
		cases[1].(map[string]any)["id"] = cases[0].(map[string]any)["id"]
	})
	inputTamper := mutateCorpus(t, raw, func(root map[string]any) {
		input := root["cases"].([]any)[0].(map[string]any)["input"].(map[string]any)
		input["declared_assignment_count"] = json.Number("0")
	})
	expectedTamper := mutateCorpus(t, raw, func(root map[string]any) {
		expected := root["cases"].([]any)[0].(map[string]any)["expected"].(map[string]any)
		lanes := expected["lanes"].(map[string]any)
		lanes["lightpanda"].(map[string]any)["desired_concurrency"] = json.Number("4")
	})
	digestTamper := mutateCorpus(t, raw, func(root map[string]any) {
		root["cases"].([]any)[0].(map[string]any)["result_digest"] = strings.Repeat("0", 64)
	})
	reordered := bytes.Replace(raw,
		[]byte(`{"cases":`),
		[]byte(`{"format":"jobseek.browser-lanes.v1.conformance/v1","cases":`), 1)
	reordered = bytes.Replace(reordered,
		[]byte(`],"format":"jobseek.browser-lanes.v1.conformance/v1"}`), []byte(`]}`), 1)

	for name, candidate := range map[string][]byte{
		"duplicate key":   duplicateKey,
		"duplicate id":    duplicateID,
		"input tamper":    inputTamper,
		"expected tamper": expectedTamper,
		"digest tamper":   digestTamper,
		"key order":       reordered,
		"missing LF":      bytes.TrimSuffix(raw, []byte("\n")),
		"extra LF":        append(append([]byte{}, raw...), '\n'),
		"whitespace":      bytes.Replace(raw, []byte(`{"cases":`), []byte("{ \"cases\":"), 1),
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeCorpus(candidate); !errorsIsInvalid(err) {
				t.Fatalf("error = %v; want fixed invalid_input", err)
			}
		})
	}
}

func TestStrictInputDecoderRejectsMalformedCanonicalAndPrivateData(t *testing.T) {
	_, corpus := loadCorpus(t)
	valid := corpus.Cases[0].Input
	if _, err := DecodeInput(valid); err != nil {
		t.Fatalf("control input rejected: %v", err)
	}
	unknown := mutateInput(t, valid, func(root map[string]any) { root["future"] = json.Number("0") })
	reordered := bytes.Replace(valid,
		[]byte(`{"capability_census_revision":"census-1","config_revision":"config-1"`),
		[]byte(`{"config_revision":"config-1","capability_census_revision":"census-1"`), 1)
	vectors := map[string][]byte{
		"duplicate root key": bytes.Replace(valid, []byte("{"), []byte(`{"now":1000,`), 1),
		"unknown key":        unknown,
		"wrong order":        reordered,
		"whitespace":         append([]byte(" "), valid...),
		"final LF":           append(append([]byte{}, valid...), '\n'),
		"trailing JSON":      append(append([]byte{}, valid...), []byte("{}")...),
		"exponent":           bytes.Replace(valid, []byte(":1,"), []byte(":1e0,"), 1),
		"redundant decimal":  bytes.Replace(valid, []byte(":0.0,"), []byte(":0.00,"), 1),
		"NaN":                bytes.Replace(valid, []byte(":0.0,"), []byte(":NaN,"), 1),
		"wrong root":         []byte("[]"),
		"missing fields":     []byte("{}"),
		"oversized":          bytes.Repeat([]byte("x"), maxDocument+1),
		"overdepth":          append(append(bytes.Repeat([]byte("["), maxDepth+1), '0'), bytes.Repeat([]byte("]"), maxDepth+1)...),
		"privacy key":        []byte(`{"secret":0}`),
	}
	for name, raw := range vectors {
		t.Run(name, func(t *testing.T) {
			if _, err := DecodeInput(raw); !errorsIsInvalid(err) {
				t.Fatalf("DecodeInput error = %v", err)
			}
			result := EvaluateDocument(raw)
			if !bytes.Equal(result, []byte(`{"error":"invalid_input"}`)) {
				t.Fatalf("unsafe result %q", result)
			}
			if bytes.Contains(result, raw[:minInt(len(raw), 8)]) && len(raw) > 8 {
				t.Fatalf("result reflected input fragment: %q", result)
			}
		})
	}
}

func TestEveryPrivacyFragmentIsRejectedWithoutReflection(t *testing.T) {
	_, corpus := loadCorpus(t)
	valid := corpus.Cases[0].Input
	fragments := []string{
		"\x7f", "scheme://private", "www.invalid", "user@host", "query?x", "hash#x",
		"path/part", `path\part`, "127.0.0.1", "[::1]", "alpha.example",
		"authorization", "bearer", "token", "secret", "password", "apikey",
		"api_key", "cookie", "session", "key=value",
	}
	for _, fragment := range fragments {
		t.Run(strings.ReplaceAll(fragment, "/", "_"), func(t *testing.T) {
			raw := mutateInput(t, valid, func(root map[string]any) {
				root["routing_revision"] = fragment
			})
			result := EvaluateDocument(raw)
			if !bytes.Equal(result, []byte(`{"error":"invalid_input"}`)) || bytes.Contains(result, []byte(fragment)) {
				t.Fatalf("unsafe result %q", result)
			}
		})
	}
}

func TestIndependentAdversarialSemanticAssertions(t *testing.T) {
	_, corpus := loadCorpus(t)
	assertReasons(t, corpus, "all_applicable_safety_freezes_sorted", "lightpanda", []string{
		"error_budget_exhausted", "resource_saturation", "service_error", "telemetry_stale",
	})
	assignmentReasons := []string{
		"assignment_invalid", "assignment_lane_mismatch", "assignment_mutated",
		"fallback_attempted", "policy_violation", "revision_mismatch",
	}
	assertReasons(t, corpus, "all_applicable_assignment_freezes_sorted", "lightpanda", assignmentReasons)
	assertReasons(t, corpus, "all_applicable_assignment_freezes_sorted", "chromium", assignmentReasons)
	for _, caseID := range []string{
		"conservation_shared_cross_lane_fence", "conservation_cross_lane_queue_occupancy",
		"conservation_duplicate_ordinal_cross_lane", "conservation_ready_inflight_overlap",
	} {
		assertHasReason(t, corpus, caseID, "lightpanda", "conservation_failure")
		assertHasReason(t, corpus, caseID, "chromium", "conservation_failure")
	}
	assertReasons(t, corpus, "zero_proof_queue_revision_mismatch", "lightpanda", []string{
		"no_eligible_backlog", "zero_proof_revision_mismatch",
	})
	assertReasons(t, corpus, "zero_proof_demand_present", "lightpanda", []string{
		"no_eligible_backlog", "zero_proof_demand_present",
	})
	for state, reason := range map[string]string{
		"error": "service_error", "full": "service_full",
		"unready": "service_unready", "unsupported": "service_unsupported",
	} {
		assertReasons(t, corpus, "service_"+state, "lightpanda", []string{reason})
	}
	for _, caseID := range []string{
		"fallback_lightpanda_to_chromium_blocked", "fallback_chromium_to_lightpanda_blocked",
	} {
		for _, lane := range laneNames {
			decision := expectedResult(t, findCase(t, corpus, caseID)).Lanes[lane]
			if decision.Decision != "freeze" || decision.SelectedItemIndex != nil || !contains(decision.Reasons, "fallback_attempted") {
				t.Fatalf("%s/%s allowed fallback: %+v", caseID, lane, decision)
			}
		}
	}
	invalid := findCase(t, corpus, "malformed_semantic_unknown_key")
	if !bytes.Equal(EvaluateDocument(invalid.Input), []byte(`{"error":"invalid_input"}`)) {
		t.Fatal("semantic malformed case did not return fixed error")
	}
}

func TestCanonicalResultBytesAndDigestAreStable(t *testing.T) {
	_, corpus := loadCorpus(t)
	for _, caseID := range []string{"claim_lightpanda_ready_only", "claim_chromium_ready_only"} {
		testCase := findCase(t, corpus, caseID)
		input, err := DecodeInput(testCase.Input)
		if err != nil {
			t.Fatal(err)
		}
		result := Evaluate(input)
		canonical, err := CanonicalBytes(result)
		if err != nil || !bytes.Equal(canonical, testCase.Expected) {
			t.Fatalf("canonical result mismatch: %v, %s", err, canonical)
		}
		digest, err := Digest(result)
		if err != nil || digest != testCase.ResultDigest {
			t.Fatalf("digest = %q, %v; want %q", digest, err, testCase.ResultDigest)
		}
		for _, decision := range result.Lanes {
			if decision.Decision == "claim" && (decision.SelectedItemIndex == nil || len(decision.Reasons) != 0) {
				t.Fatalf("invalid claim: %+v", decision)
			}
		}
	}
}

func TestValidZeroProofMayCrossWarmFloorWithoutOrdinaryScaleDown(t *testing.T) {
	_, corpus := loadCorpus(t)
	zeroCase := findCase(t, corpus, "zero_proof_valid_reaches_zero")
	input, err := DecodeInput(zeroCase.Input)
	if err != nil {
		t.Fatal(err)
	}
	input.Lanes[0].Capacity.WarmFloor = 1
	decision := Evaluate(input).Lanes["lightpanda"]
	if decision.DesiredConcurrency != 0 || strings.Join(decision.Reasons, "|") != "no_eligible_backlog" {
		t.Fatalf("proof-gated zero did not cross warm floor: %+v", decision)
	}
	holdCase := findCase(t, corpus, "zero_proof_does_not_create_ordinary_scale_down")
	holdInput, err := DecodeInput(holdCase.Input)
	if err != nil {
		t.Fatal(err)
	}
	hold := Evaluate(holdInput).Lanes["lightpanda"]
	if hold.DesiredConcurrency != 2 {
		t.Fatalf("zero proof manufactured ordinary scale-down: %+v", hold)
	}
}

func loadCorpus(t *testing.T) ([]byte, Corpus) {
	t.Helper()
	raw := mustRead(t, fixturePath)
	corpus, err := DecodeCorpus(raw)
	if err != nil {
		t.Fatal(err)
	}
	if err := VerifyCorpusSidecar(raw, mustRead(t, sidecarPath)); err != nil {
		t.Fatal(err)
	}
	return raw, corpus
}

func mustRead(t *testing.T, path string) []byte {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func mutateCorpus(t *testing.T, raw []byte, mutate func(map[string]any)) []byte {
	t.Helper()
	value, err := decodeAny(bytes.TrimSuffix(raw, []byte("\n")))
	if err != nil {
		t.Fatal(err)
	}
	root := value.(map[string]any)
	mutate(root)
	canonical, err := canonicalValue(root)
	if err != nil {
		t.Fatal(err)
	}
	return append(canonical, '\n')
}

func mutateInput(t *testing.T, raw []byte, mutate func(map[string]any)) []byte {
	t.Helper()
	value, err := decodeAny(raw)
	if err != nil {
		t.Fatal(err)
	}
	root := value.(map[string]any)
	mutate(root)
	canonical, err := canonicalValue(root)
	if err != nil {
		t.Fatal(err)
	}
	return canonical
}

func findCase(t *testing.T, corpus Corpus, id string) Case {
	t.Helper()
	for _, testCase := range corpus.Cases {
		if testCase.ID == id {
			return testCase
		}
	}
	t.Fatalf("missing case %q", id)
	return Case{}
}

func expectedResult(t *testing.T, testCase Case) Result {
	t.Helper()
	var result Result
	if err := json.Unmarshal(testCase.Expected, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func assertReasons(t *testing.T, corpus Corpus, caseID, lane string, expected []string) {
	t.Helper()
	actual := expectedResult(t, findCase(t, corpus, caseID)).Lanes[lane].Reasons
	if strings.Join(actual, "|") != strings.Join(expected, "|") || !sort.StringsAreSorted(actual) {
		t.Fatalf("%s/%s reasons = %v; want %v", caseID, lane, actual, expected)
	}
}

func assertHasReason(t *testing.T, corpus Corpus, caseID, lane, reason string) {
	t.Helper()
	actual := expectedResult(t, findCase(t, corpus, caseID)).Lanes[lane]
	if actual.Decision != "freeze" || actual.SelectedItemIndex != nil || !contains(actual.Reasons, reason) {
		t.Fatalf("%s/%s = %+v; want freeze with %s", caseID, lane, actual, reason)
	}
}

func contains(values []string, expected string) bool {
	for _, value := range values {
		if value == expected {
			return true
		}
	}
	return false
}

func errorsIsInvalid(err error) bool { return err != nil && err.Error() == "invalid_input" }

func minInt(left, right int) int {
	if left < right {
		return left
	}
	return right
}
