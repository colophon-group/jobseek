package adjacentpolicy

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

var mandatoryPrivacyCaseIDs = map[string]bool{
	"redact_api_key":                       true,
	"redact_authentication":                true,
	"redact_basic":                         true,
	"redact_basic_scalar":                  true,
	"redact_bearer":                        true,
	"redact_bearer_scalar":                 true,
	"redact_camel_api_key":                 true,
	"redact_case_variant":                  true,
	"redact_email_json":                    true,
	"redact_envelope_inline":               true,
	"redact_envelope_metadata":             true,
	"redact_form_secret":                   true,
	"redact_json_secret_key":               true,
	"redact_secret_access_key":             true,
	"redact_secret_query":                  true,
	"redact_separator_dot":                 true,
	"redact_separator_ff":                  true,
	"redact_separator_space":               true,
	"redact_separator_underscore":          true,
	"redact_separator_vt":                  true,
	"redact_set_cookie":                    true,
	"redact_url_userinfo":                  true,
	"redact_unicode_escaped_email":         true,
	"redact_whole_base64_wrapper":          true,
	"redact_whole_percent_wrapper":         true,
	"redact_x_api_token":                   true,
	"redact_x_secret":                      true,
	"reject_chunk_artifact":                true,
	"reject_chunks_incomplete":             true,
	"reject_chunks_misordered":             true,
	"reject_chunks_wrong_digest":           true,
	"reject_chunks_wrong_size":             true,
	"reject_chunks_wrong_total":            true,
	"reject_malformed_base64":              true,
	"reject_malformed_form":                true,
	"reject_malformed_headers":             true,
	"reject_malformed_json":                true,
	"reject_malformed_percent":             true,
	"reject_malformed_url":                 true,
	"reject_envelope_inner_extreme_depth":  true,
	"reject_envelope_inner_lone_surrogate": true,
	"reject_envelope_outer_duplicate_key":  true,
	"reject_json_duplicate_key":            true,
	"reject_json_extreme_depth":            true,
	"reject_json_lone_surrogate":           true,
	"reject_unavailable_envelope_artifact": true,
	"reject_unknown_context":               true,
	"reject_unknown_envelope_encoding":     true,
	"reject_unknown_envelope_schema":       true,
	"reject_unknown_envelope_version":      true,
	"reject_unknown_wrapper":               true,
	"safe_base64_wrapper":                  true,
	"safe_extension_envelope":              true,
	"safe_form":                            true,
	"safe_header_colon_value":              true,
	"safe_headers":                         true,
	"safe_json":                            true,
	"safe_json_u2028":                      true,
	"safe_envelope_inline_u2029":           true,
	"safe_leading_separator_key":           true,
	"safe_percent_wrapper":                 true,
	"safe_url":                             true,
	"safe_trailing_separator_key":          true,
	"split_base64_wrapper":                 true,
	"split_form_key":                       true,
	"split_header_key":                     true,
	"split_header_value":                   true,
	"split_json_email":                     true,
	"split_json_key":                       true,
	"split_percent_wrapper":                true,
	"split_url_query":                      true,
	"chunk_count_limit":                    true,
	"chunk_count_limit_minus_1":            true,
	"chunk_count_limit_plus_1":             true,
	"encoded_input_limit":                  true,
	"encoded_input_limit_minus_1":          true,
	"encoded_input_limit_plus_1":           true,
	"json_depth_limit":                     true,
	"json_depth_limit_minus_1":             true,
	"json_depth_limit_plus_1":              true,
	"structured_items_limit":               true,
	"structured_items_limit_minus_1":       true,
	"structured_items_limit_plus_1":        true,
}

func TestPrivacyCorpusMatchesEveryExpectedResultAndDigest(t *testing.T) {
	validator, corpus, err := loadPrivacyAssets(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	seen := map[string]bool{}
	for _, item := range corpus.Cases {
		if seen[item.CaseID] {
			t.Fatalf("duplicate privacy case ID: %s", item.CaseID)
		}
		seen[item.CaseID] = true
		actual := safePrivacyResult(item.CaseID, validator.transform(item))
		actualJSON, err := canonicalJSON(actual)
		if err != nil {
			t.Fatal(err)
		}
		expectedJSON, err := canonicalJSON(item.Expected)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(actualJSON, expectedJSON) {
			t.Fatalf("privacy result mismatch for %s\nactual=%s\nexpected=%s", item.CaseID, actualJSON, expectedJSON)
		}
		digest, err := expectedPrivacyDigest(item.Expected)
		if err != nil {
			t.Fatal(err)
		}
		if digest != item.ResultDigest {
			t.Fatalf("safe result digest mismatch for %s", item.CaseID)
		}
	}
	if !reflect.DeepEqual(seen, mandatoryPrivacyCaseIDs) {
		t.Fatalf("mandatory privacy case set drifted: seen=%v mandatory=%v", seen, mandatoryPrivacyCaseIDs)
	}
}

func TestPrivacyManifestHardCodesTheSameCompleteCaseSet(t *testing.T) {
	_, corpus, err := loadPrivacyAssets(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	declared := map[string]bool{}
	for _, caseID := range corpus.RequiredCaseIDs {
		if declared[caseID] {
			t.Fatalf("duplicate required privacy case: %s", caseID)
		}
		declared[caseID] = true
	}
	if !reflect.DeepEqual(declared, mandatoryPrivacyCaseIDs) {
		t.Fatalf("manifest required case set drifted")
	}
}

func TestPrivacyTransformIsDeterministicAndRejectsWithoutInputMaterial(t *testing.T) {
	validator, corpus, err := loadPrivacyAssets(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range corpus.Cases {
		first := validator.transform(item)
		second := validator.transform(item)
		if !reflect.DeepEqual(first, second) {
			t.Fatalf("non-deterministic privacy result: %s", item.CaseID)
		}
		if first.Status != "rejected" {
			continue
		}
		safe, err := canonicalJSON(safePrivacyResult(item.CaseID, first))
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(safe), "SYNTHETIC_REJECTED_CANARY") || len(first.Output) != 0 || len(first.Findings) != 0 {
			t.Fatalf("rejected privacy result leaked input-derived material: %s", item.CaseID)
		}
	}
}

func TestPrivacyDominatedLimitComparatorsAreInclusive(t *testing.T) {
	_, corpus, err := loadPrivacyAssets(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	expectedIDs := []string{
		"decoded_working_set_limit",
		"decoded_working_set_limit_minus_1",
		"decoded_working_set_limit_plus_1",
		"output_limit",
		"output_limit_minus_1",
		"output_limit_plus_1",
	}
	actualIDs := make([]string, 0, len(corpus.HelperLimitVectors))
	for _, vector := range corpus.HelperLimitVectors {
		actualIDs = append(actualIDs, vector.ID)
		if accepted := vector.Observed <= vector.Limit; accepted != vector.Accepted {
			t.Fatalf("helper limit boundary mismatch: %s", vector.ID)
		}
	}
	sort.Strings(actualIDs)
	if !reflect.DeepEqual(actualIDs, expectedIDs) {
		t.Fatalf("helper limit vector set drifted: %v", actualIDs)
	}
}

func TestPrivacyGeneratorCheckIsOfflineAndExact(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Fatal("python3 is required for privacy generator conformance")
	}
	root := contractRoot(t)
	command := exec.Command(python, filepath.Join(root, "tools", "generate_privacy.py"), "--check") // #nosec G204 -- fixed executable and repository path.
	command.Env = append(os.Environ(), "NO_PROXY=*", "no_proxy=*")
	if output, err := command.CombinedOutput(); err != nil {
		t.Fatalf("privacy generator check failed: %v\n%s", err, output)
	}
	sources := []string{
		filepath.Join(root, "tools", "generate_privacy.py"),
		filepath.Join(root, "conformance", "go", "redaction.go"),
	}
	for _, path := range sources {
		source, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		for _, forbidden := range []string{"net/http", "urlopen(", "requests.", "httpx."} {
			if bytes.Contains(source, []byte(forbidden)) {
				t.Fatalf("privacy conformance contains network capability %q", forbidden)
			}
		}
	}
}

func TestPrivacyRegistryHasOnlyClosedStatusesContextsAndErrors(t *testing.T) {
	validator, _, err := loadPrivacyAssets(contractRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(validator.registry.Statuses, []string{"unchanged", "transformed", "rejected"}) {
		t.Fatalf("privacy statuses are not closed: %v", validator.registry.Statuses)
	}
	if !reflect.DeepEqual(validator.registry.Contexts, []string{"headers", "url", "json", "form", "extension_envelope"}) {
		t.Fatalf("privacy contexts are not closed: %v", validator.registry.Contexts)
	}
	if !reflect.DeepEqual(validator.registry.RejectedCodes, []string{"unknown_context", "malformed_encoding", "unsupported_envelope", "artifact_unavailable", "limit_exceeded", "invalid_chunks"}) {
		t.Fatalf("privacy error registry is not closed: %v", validator.registry.RejectedCodes)
	}
}

func TestPrivacyManifestContainsOnlySyntheticCanaries(t *testing.T) {
	data, err := os.ReadFile(filepath.Join(contractRoot(t), "fixtures", "redaction", "manifest.json"))
	if err != nil {
		t.Fatal(err)
	}
	var value any
	if err := json.Unmarshal(data, &value); err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(data, []byte("@gmail.com")) || bytes.Contains(data, []byte("@outlook.com")) {
		t.Fatal("privacy fixture contains a non-reserved email domain")
	}
}
