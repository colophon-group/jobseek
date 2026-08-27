package adjacentpolicy

import (
	"bytes"
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strings"
	"testing"
)

var semanticsRequiredCaseIDs = []string{
	"safe_scrape_projected",
	"invalid_visible_content",
	"safe_monitor_url_only",
	"rich_monitor",
	"identity_absent_legacy",
	"explicit_identity_projected",
	"explicit_null_identity_legacy",
	"identity_url_union_lockstep",
	"identity_url_churn_winner",
	"identity_url_churn_permutation",
	"same_url_same_identity_dedupe",
	"same_url_conflicting_identities",
	"mixed_identity_distinct_urls",
	"mixed_identity_same_url_collision",
	"same_identity_divergent_content",
	"malformed_source_identity",
	"existing_effect_identity_alignment",
	"invalid_existing_effect_identity",
	"suppressed_precondition",
	"invalid_url",
	"locale_alias",
	"locale_rejection",
	"canonical_collision",
	"ordered_metadata",
	"absent_vs_empty",
	"digest_sensitivity",
	"localized_only_visible",
	"invalid_html_unclosed_comment",
	"invalid_html_unclosed_quote",
	"invalid_html_unclosed_suppressed",
	"invalid_html_nesting_limit",
	"invalid_url_fragment_escape",
	"invalid_url_port_zero",
	"invalid_url_legacy_ip",
	"invalid_url_above_root",
	"safe_url_query_distinctions",
	"safe_url_default_repeated_slash",
	"invalid_url_non_ascii_host",
	"browser_suppressed",
	"unknown_subject_rejected",
	"language_alias",
	"language_rejection",
	"locale_collision",
	"invalid_shape_unknown",
	"invalid_shape_missing",
	"invalid_projection_alignment",
	"monitor_batches_ordered",
	"monitor_batches_sitemap_conflict",
	"monitor_batches_counter_overflow",
	"monitor_incomplete",
	"privacy_rejected",
	"rich_url_union_lockstep",
	"divergent_rich_collision",
	"set_permutation_dedupe",
	"safe_url_leading_zero_default_port",
	"invalid_projection_malformed_url",
	"invalid_localized_description",
	"visible_unterminated_space_entities",
	"invalid_unicode_surrogate",
	"invalid_metadata_null",
	"invalid_localized_description_null",
	"invalid_url_legacy_mixed_components",
	"safe_url_numeric_overrange_dns",
	"invalid_precondition_type",
}

type semanticsManifest struct {
	Cases           []map[string]any `json:"cases"`
	Format          string           `json:"format"`
	RequiredCaseIDs []string         `json:"required_case_ids"`
}

func loadSemanticsManifest(t *testing.T) semanticsManifest {
	t.Helper()
	content, err := os.ReadFile(
		filepath.Join(contractRoot(t), "fixtures", "semantics", "manifest.json"),
	)
	if err != nil {
		t.Fatal(err)
	}
	var raw struct {
		Cases           []json.RawMessage `json:"cases"`
		Format          string            `json:"format"`
		RequiredCaseIDs []string          `json:"required_case_ids"`
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.UseNumber()
	if err := decoder.Decode(&raw); err != nil {
		t.Fatal(err)
	}
	manifest := semanticsManifest{
		Cases:           make([]map[string]any, 0, len(raw.Cases)),
		Format:          raw.Format,
		RequiredCaseIDs: raw.RequiredCaseIDs,
	}
	for _, rawCase := range raw.Cases {
		unicodeValid := semanticsRawJSONUnicodeValid(rawCase)
		var item map[string]any
		decoder := json.NewDecoder(bytes.NewReader(rawCase))
		decoder.UseNumber()
		if err := decoder.Decode(&item); err != nil {
			t.Fatal(err)
		}
		if !unicodeValid {
			input, ok := item["input"].(map[string]any)
			if !ok {
				t.Fatal("invalid-Unicode case has no input object")
			}
			input["__invalid_unicode_json"] = string([]byte{0xff})
		}
		manifest.Cases = append(manifest.Cases, item)
	}
	return manifest
}

func semanticsCaseByID(t *testing.T, manifest semanticsManifest, caseID string) map[string]any {
	t.Helper()
	for _, item := range manifest.Cases {
		if item["id"] == caseID {
			return item
		}
	}
	t.Fatalf("missing semantics case %q", caseID)
	return nil
}

func semanticsJSONForTest(t *testing.T, value any) []byte {
	t.Helper()
	content, err := semanticsCanonicalJSON(value)
	if err != nil {
		t.Fatal(err)
	}
	return content
}

func TestSemanticsRequiredIDsAreHardCodedAndComplete(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	if manifest.Format != "jobseek.runtime.semantics-corpus/v1" {
		t.Fatalf("unexpected semantics format %q", manifest.Format)
	}
	if !reflect.DeepEqual(manifest.RequiredCaseIDs, semanticsRequiredCaseIDs) {
		t.Fatalf("manifest required IDs differ\n got: %v\nwant: %v", manifest.RequiredCaseIDs, semanticsRequiredCaseIDs)
	}
	if len(manifest.Cases) != 64 {
		t.Fatalf("semantics case count = %d, want 64", len(manifest.Cases))
	}
	seen := map[string]bool{}
	for index, item := range manifest.Cases {
		caseID, ok := item["id"].(string)
		if !ok || caseID != semanticsRequiredCaseIDs[index] || seen[caseID] {
			t.Fatalf("invalid case ID/order at %d: %v", index, item["id"])
		}
		seen[caseID] = true
		if len(item) != 4 {
			t.Fatalf("case %q has %d fields, want exact four-field shape", caseID, len(item))
		}
	}
}

func TestSemanticsManifestMatchesEveryExactGoResultAndDigest(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	projected := 0
	for _, item := range manifest.Cases {
		caseID := item["id"].(string)
		t.Run(caseID, func(t *testing.T) {
			actual := ProjectSemantics(item)
			expected, ok := item["expected"].(map[string]any)
			if !ok {
				t.Fatal("expected result is not an object")
			}
			actualJSON := semanticsJSONForTest(t, map[string]any(actual))
			expectedJSON := semanticsJSONForTest(t, expected)
			if !bytes.Equal(actualJSON, expectedJSON) {
				t.Fatalf("result mismatch\n got: %s\nwant: %s", actualJSON, expectedJSON)
			}
			if expected["status"] != "projected" {
				if len(expected) != 3 {
					t.Fatalf("stopped result leaked fields: %v", expected)
				}
				return
			}
			projected++
			withoutDigest := make(map[string]any, len(expected)-1)
			for key, value := range expected {
				if key != "semantic_sha256" {
					withoutDigest[key] = value
				}
			}
			digest, err := semanticsResultSHA256(withoutDigest)
			if err != nil {
				t.Fatal(err)
			}
			if digest != expected["semantic_sha256"] {
				t.Fatalf("semantic digest = %s, want %v", digest, expected["semantic_sha256"])
			}
		})
	}
	if projected != 26 {
		t.Fatalf("projected result count = %d, want 26", projected)
	}
}

func TestSemanticsProjectedTargetsRemainAtomicallyAligned(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	for _, item := range manifest.Cases {
		result := ProjectSemantics(item)
		if result["status"] != "projected" {
			continue
		}
		effects := result["projected_effects"].(map[string]any)
		urls := effects["urls_to_upsert"].([]any)
		hashes := effects["content_hashes"].([]any)
		jobs := effects["job_effects"].([]any)
		targets := effects["targets"].([]any)
		if len(urls) != len(hashes) || len(urls) != len(jobs) || len(urls) != len(targets) {
			t.Fatalf("case %v has detached projection lists", item["id"])
		}
		for index := range urls {
			job := jobs[index].(map[string]any)
			target := targets[index].(map[string]any)
			if len(job) != 2 && len(job) != 3 {
				t.Fatalf("case %v target %d has open job effect", item["id"], index)
			}
			if identityValue, present := job["source_identity"]; present {
				if _, failure := semanticsSourceIdentity(identityValue, false); failure != nil {
					t.Fatalf("case %v target %d has invalid source identity", item["id"], index)
				}
			}
			if urls[index] != job["source_url"] || urls[index] != target["url"] {
				t.Fatalf("case %v target %d detached URL", item["id"], index)
			}
			targetHash, _ := target["content_sha256"]
			if targetHash == nil {
				targetHash = ""
			}
			if hashes[index] != job["content_sha256"] || hashes[index] != targetHash {
				t.Fatalf("case %v target %d detached hash", item["id"], index)
			}
		}
	}
}

func TestSemanticsSourceIdentityProjectionAndMixedModeRules(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	effects := func(caseID string) map[string]any {
		result := ProjectSemantics(semanticsCaseByID(t, manifest, caseID))
		if result["status"] != "projected" {
			t.Fatalf("case %q stopped: %v", caseID, result)
		}
		return result["projected_effects"].(map[string]any)
	}

	explicit := effects("explicit_identity_projected")
	explicitJob := explicit["job_effects"].([]any)[0].(map[string]any)
	if explicitJob["source_identity"] != "smartrecruiters:synthetic:42" ||
		explicitJob["source_url"] != "https://jobs.example.invalid/openings/identity-z" {
		t.Fatalf("explicit identity detached from outbound URL: %v", explicitJob)
	}

	winner := effects("identity_url_churn_winner")
	permutation := effects("identity_url_churn_permutation")
	if !bytes.Equal(semanticsJSONForTest(t, winner), semanticsJSONForTest(t, permutation)) {
		t.Fatalf("URL churn result depends on input order\nfirst: %v\nsecond: %v", winner, permutation)
	}
	winnerURLs := winner["urls_to_upsert"].([]any)
	if !reflect.DeepEqual(winnerURLs, []any{"https://jobs.example.invalid/openings/identity-a"}) {
		t.Fatalf("URL churn winner = %v", winnerURLs)
	}

	for _, caseID := range []string{"identity_absent_legacy", "explicit_null_identity_legacy"} {
		job := effects(caseID)["job_effects"].([]any)[0].(map[string]any)
		if _, present := job["source_identity"]; present {
			t.Fatalf("legacy case %q gained identity: %v", caseID, job)
		}
	}

	mixed := effects("mixed_identity_distinct_urls")
	mixedURLs := mixed["urls_to_upsert"].([]any)
	wantMixedURLs := []any{
		"https://jobs.example.invalid/openings/identity-z",
		"https://jobs.example.invalid/openings/identity-a",
	}
	if !reflect.DeepEqual(mixedURLs, wantMixedURLs) {
		t.Fatalf("mixed logical-key order = %v, want %v", mixedURLs, wantMixedURLs)
	}

	stopped := map[string]map[string]any{
		"same_url_conflicting_identities":   {"reason": "canonical_collision", "status": "suppressed"},
		"mixed_identity_same_url_collision": {"reason": "canonical_collision", "status": "suppressed"},
		"same_identity_divergent_content":   {"reason": "canonical_collision", "status": "suppressed"},
		"malformed_source_identity":         {"reason": "invalid_projection", "status": "rejected"},
		"invalid_existing_effect_identity":  {"reason": "invalid_projection", "status": "rejected"},
	}
	for caseID, expected := range stopped {
		result := ProjectSemantics(semanticsCaseByID(t, manifest, caseID))
		if result["status"] != expected["status"] || result["reason"] != expected["reason"] {
			t.Fatalf("case %q result = %v", caseID, result)
		}
	}
}

func TestSemanticsVisibilityURLAndLocaleProfiles(t *testing.T) {
	visibility := map[string]bool{
		"<p>Synthetic role</p>":                true,
		"&copy;":                               true,
		"&nbsp;&#160;&#xA0;\u200b":             false,
		"&nbsp;":                               false,
		"&#160;":                               false,
		"&#xA0;":                               false,
		"&nbsp":                                true,
		"&#160":                                true,
		"&#xA0":                                true,
		"<script>visible-looking</script>":     false,
		"<style>visible-looking</style>":       false,
		"<template>visible-looking</template>": false,
		"<noscript>visible-looking</noscript>": false,
		"<p hidden>visible-looking</p>":        false,
		"<p aria-hidden=TRUE>visible-looking</p>":       false,
		"<p style='DISPLAY: none'>hidden text</p>":      false,
		"<p style='visibility: hidden'>hidden text</p>": false,
		"<p style=visibility:collapse>hidden text</p>":  false,
	}
	for source, expected := range visibility {
		actual, failure := semanticsHasVisibleContent(source)
		if failure != nil || actual != expected {
			t.Fatalf("visibility %q = (%v, %v), want %v", source, actual, failure, expected)
		}
	}
	invalidHTML := []string{
		"<section hidden>unclosed",
		"<script>unclosed",
		"<p>Visible</p><!-- unclosed",
		"<p hidden='unterminated>visible",
		"visible\x00control",
		strings.Repeat("<div>", 129) + "visible" + strings.Repeat("</div>", 129),
		strings.Repeat("x", semanticsMaxHTMLBytes+1),
	}
	for index, source := range invalidHTML {
		if _, failure := semanticsHasVisibleContent(source); failure == nil || failure.reason != "invalid_visible_content" {
			t.Fatalf("invalid HTML %d failure = %v", index, failure)
		}
	}
	canonical, failure := semanticsCanonicalURL("HTTPS://JOBS.EXAMPLE.INVALID:443/a/./b?z=2&a=&a#fragment")
	if failure != nil || canonical != "https://jobs.example.invalid/a/b?a&a=&z=2" {
		t.Fatalf("canonical URL = %q, %v", canonical, failure)
	}
	if locale, failure := semanticsCanonicalLocale("EN_us"); failure != nil || locale != "en-US" {
		t.Fatalf("canonical locale = %q, %v", locale, failure)
	}
	canonicalLocales := map[string]bool{}
	for _, locale := range semanticsLocales {
		canonicalLocales[locale] = true
	}
	if len(canonicalLocales) != 13 {
		t.Fatalf("canonical locale count = %d, want 13", len(canonicalLocales))
	}
	for locale := range canonicalLocales {
		if actual, failure := semanticsCanonicalLocale(locale); failure != nil || actual != locale {
			t.Fatalf("canonical locale %q = %q, %v", locale, actual, failure)
		}
	}
}

func TestSemanticsURLProfileRejectsTheClosedInvalidSet(t *testing.T) {
	invalid := []string{
		"https://user@jobs.example.invalid/role",
		"https://jobs.example.invalid:0/role",
		"https://jobs.example.invalid:65536/role",
		"http://127.0.0.1/role",
		"http://0177.0.0.1/role",
		"http://0x7f.0.0.1/role",
		"http://127.0x0.0.1/role",
		"http://2130706433/role",
		"https://jobs.example.invalid/../../role",
		"https://jöbs.example.invalid/role",
		"https://jobs.example.invalid./role",
		"https://jobs.example.invalid/role#bad%ZZ",
		"https://jobs.example.invalid\\role",
		"https://jobs.example.invalid/role with-space",
		"https://jobs.example.invalid/role\tcontrol",
	}
	for _, source := range invalid {
		if _, failure := semanticsCanonicalURL(source); failure == nil || failure.reason != "invalid_url" {
			t.Fatalf("invalid URL %q failure = %v", source, failure)
		}
	}
	valid := map[string]string{
		"https://jobs.example.invalid":           "https://jobs.example.invalid/",
		"https://jobs.example.invalid//a///b":    "https://jobs.example.invalid//a///b",
		"https://jobs.example.invalid/?a=&a":     "https://jobs.example.invalid/?a&a=",
		"HTTP://JOBS.EXAMPLE.INVALID:80/role":    "http://jobs.example.invalid/role",
		"HTTP://JOBS.EXAMPLE.INVALID:080/role":   "http://jobs.example.invalid/role",
		"HTTPS://JOBS.EXAMPLE.INVALID:0443/role": "https://jobs.example.invalid/role",
		"https://jobs.example.invalid/%7erole":   "https://jobs.example.invalid/~role",
		"https://4294967296/role":                "https://4294967296/role",
		"https://256.0.0.1/role":                 "https://256.0.0.1/role",
	}
	for source, expected := range valid {
		actual, failure := semanticsCanonicalURL(source)
		if failure != nil || actual != expected {
			t.Fatalf("valid URL %q = %q, %v; want %q", source, actual, failure, expected)
		}
	}
}

func TestSemanticsConsistencyRepairCasesHaveClosedOutcomes(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	for _, expectation := range []struct {
		caseID string
		reason string
		status string
	}{
		{"invalid_projection_malformed_url", "invalid_projection", "rejected"},
		{"invalid_localized_description", "invalid_visible_content", "suppressed"},
		{"invalid_unicode_surrogate", "invalid_projection", "rejected"},
		{"invalid_metadata_null", "invalid_projection", "rejected"},
		{"invalid_localized_description_null", "invalid_projection", "rejected"},
	} {
		result := ProjectSemantics(semanticsCaseByID(t, manifest, expectation.caseID))
		if len(result) != 3 || result["reason"] != expectation.reason || result["status"] != expectation.status {
			t.Fatalf("case %q result = %v", expectation.caseID, result)
		}
	}
}

func TestSemanticsMalformedUnicodeRejectsBeforeHTML(t *testing.T) {
	if semanticsRawJSONUnicodeValid([]byte(`{"value":"\ud800"}`)) {
		t.Fatal("raw lone-surrogate escape passed preflight")
	}
	if !semanticsRawJSONUnicodeValid([]byte(`{"value":"\ud83d\ude00"}`)) {
		t.Fatal("valid surrogate pair failed preflight")
	}
	manifest := loadSemanticsManifest(t)
	item := semanticsCaseByID(t, manifest, "safe_scrape_projected")
	input := item["input"].(map[string]any)
	content := input["result"].(map[string]any)["content"].(map[string]any)
	content["description_html"] = string([]byte{0xff})
	result := ProjectSemantics(item)
	if len(result) != 3 || result["reason"] != "invalid_projection" || result["status"] != "rejected" {
		t.Fatalf("invalid UTF-8 result = %v", result)
	}
	if _, failure := semanticsHasVisibleContent(string([]byte{0xff})); failure == nil ||
		failure.reason != "invalid_projection" || !failure.rejected {
		t.Fatalf("invalid UTF-8 HTML failure = %v", failure)
	}
}

func TestSemanticsEveryPreconditionTypePrecedesSuppression(t *testing.T) {
	invalid := map[string]any{
		"protocol_accepted":   "true",
		"terminal_status":     true,
		"eligible_for_commit": json.Number("1"),
		"batches_complete":    json.Number("1"),
		"privacy_status":      false,
	}
	for field, value := range invalid {
		manifest := loadSemanticsManifest(t)
		item := semanticsCaseByID(t, manifest, "safe_scrape_projected")
		preconditions := item["input"].(map[string]any)["preconditions"].(map[string]any)
		preconditions["privacy_status"] = "rejected"
		preconditions[field] = value
		result := ProjectSemantics(item)
		if len(result) != 3 || result["reason"] != "invalid_projection" || result["status"] != "rejected" {
			t.Fatalf("precondition %q result = %v", field, result)
		}
	}
}

func TestSemanticsCanonicalJSONRejectsNonIntegerNumbersAndInvalidUTF8(t *testing.T) {
	valid, err := semanticsCanonicalJSON(map[string]any{"a": "é", "z": "\u2028"})
	if err != nil || string(valid) != "{\"a\":\"é\",\"z\":\"\u2028\"}" {
		t.Fatalf("literal canonical JSON = %q, %v", valid, err)
	}
	for _, invalid := range []any{
		json.Number("1.5"),
		json.Number("1e3"),
		string([]byte{0xed, 0xa0, 0x80}),
	} {
		if _, err := semanticsCanonicalJSON(map[string]any{"value": invalid}); err == nil {
			t.Fatalf("canonical JSON accepted invalid value %#v", invalid)
		}
	}
}

func TestSemanticsOrderedMetadataAndContentChangesAffectDigests(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	metadataCase := semanticsCaseByID(t, manifest, "ordered_metadata")
	metadataInput := metadataCase["input"].(map[string]any)
	targetURL := metadataInput["request"].(map[string]any)["target_url"].(string)
	actualMetadata := metadataInput["result"].(map[string]any)["metadata_updates"].(map[string]any)
	comparisonMetadata := metadataInput["comparison_metadata_updates"].(map[string]any)
	actualDigest, err := semanticsMetadataSHA256(targetURL, actualMetadata)
	if err != nil {
		t.Fatal(err)
	}
	comparisonDigest, err := semanticsMetadataSHA256(targetURL, comparisonMetadata)
	if err != nil {
		t.Fatal(err)
	}
	if actualDigest == comparisonDigest {
		t.Fatal("ordered metadata permutation did not change digest")
	}
	digestCase := semanticsCaseByID(t, manifest, "digest_sensitivity")
	digestInput := digestCase["input"].(map[string]any)
	expected := digestCase["expected"].(map[string]any)
	effects := expected["projected_effects"].(map[string]any)
	if digestInput["comparison_content_sha256"] == effects["content_hashes"].([]any)[0] {
		t.Fatal("one-byte content change did not change digest")
	}
	absent := semanticsCaseByID(t, manifest, "absent_vs_empty")["expected"].(map[string]any)
	absentEffects := absent["projected_effects"].(map[string]any)
	absentHashes := absentEffects["content_hashes"].([]any)
	if absent["status"] != "projected" || len(absentHashes) != 2 || absentHashes[0] == absentHashes[1] {
		t.Fatalf("absent and present-empty did not remain distinct: %v", absent)
	}
}

func TestSemanticsEachGoneConditionKeepsSafeUpserts(t *testing.T) {
	conditions := map[string]any{
		"hybrid": true, "truncated": true,
		"filtered_count":          json.Number("1"),
		"security_filtered_count": json.Number("1"),
	}
	for field, value := range conditions {
		manifest := loadSemanticsManifest(t)
		item := semanticsCaseByID(t, manifest, "safe_monitor_url_only")
		resultInput := item["input"].(map[string]any)["result"].(map[string]any)
		resultInput[field] = value
		result := ProjectSemantics(item)
		if result["status"] != "projected" {
			t.Fatalf("gone condition %q suppressed safe upserts: %v", field, result)
		}
		effects := result["projected_effects"].(map[string]any)
		if effects["gone_detection_allowed"] != false || len(effects["targets"].([]any)) != 2 {
			t.Fatalf("gone condition %q effects = %v", field, effects)
		}
	}
}

func TestSemanticsBatchMetadataOrderAndSetCanonicalization(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	item := semanticsCaseByID(t, manifest, "monitor_batches_ordered")
	original := ProjectSemantics(item)
	batches := item["input"].(map[string]any)["batches"].([]any)
	batches[0], batches[1] = batches[1], batches[0]
	reversed := ProjectSemantics(item)
	originalHash := original["projected_effects"].(map[string]any)["metadata_updates_sha256"]
	reversedHash := reversed["projected_effects"].(map[string]any)["metadata_updates_sha256"]
	if originalHash == reversedHash {
		t.Fatal("ordered batch metadata permutation did not change digest")
	}
	first := map[string]any{
		"description_html": "<p>Visible.</p>",
		"language":         "EN_us",
		"localizations":    []any{},
		"locations":        map[string]any{"values": []any{"Zürich", "Basel", "Zürich"}},
		"skills":           []any{"sql", "python", "sql"},
	}
	second := map[string]any{
		"description_html": "<p>Visible.</p>",
		"language":         "en-US",
		"localizations":    []any{},
		"locations":        map[string]any{"values": []any{"Basel", "Zürich"}},
		"skills":           []any{"python", "sql"},
	}
	canonicalFirst, failure := semanticsCanonicalJob(first)
	if failure != nil {
		t.Fatal(failure.reason)
	}
	canonicalSecond, failure := semanticsCanonicalJob(second)
	if failure != nil {
		t.Fatal(failure.reason)
	}
	if !bytes.Equal(semanticsJSONForTest(t, canonicalFirst), semanticsJSONForTest(t, canonicalSecond)) {
		t.Fatalf("set permutation changed canonical job\nfirst: %v\nsecond: %v", canonicalFirst, canonicalSecond)
	}
}

func TestSemanticsStoppedResultsDoNotLeakSyntheticInput(t *testing.T) {
	manifest := loadSemanticsManifest(t)
	for _, caseID := range []string{"invalid_visible_content", "invalid_url", "locale_rejection"} {
		item := semanticsCaseByID(t, manifest, caseID)
		input := item["input"].(map[string]any)
		input["raw_canary"] = "SYNTHETIC_RAW_CANARY_DO_NOT_EMIT"
		result := ProjectSemantics(item)
		encoded := semanticsJSONForTest(t, map[string]any(result))
		if len(result) != 3 || bytes.Contains(encoded, []byte("SYNTHETIC_RAW_CANARY_DO_NOT_EMIT")) {
			t.Fatalf("case %q leaked stopped input: %s", caseID, encoded)
		}
	}
}

func TestSemanticsImplementationHasNoNetworkOrProcessImports(t *testing.T) {
	path := filepath.Join(contractRoot(t), "conformance", "go", "semantics.go")
	parsed, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
	if err != nil {
		t.Fatal(err)
	}
	forbidden := map[string]bool{
		"net/http": true, "os/exec": true, "syscall": true,
	}
	imports := make([]string, 0, len(parsed.Imports))
	for _, specification := range parsed.Imports {
		path, err := strconvUnquote(specification.Path.Value)
		if err != nil {
			t.Fatal(err)
		}
		imports = append(imports, path)
		if forbidden[path] {
			t.Fatalf("forbidden semantics import %q", path)
		}
	}
	sort.Strings(imports)
	if ast.FileExports(parsed) && strings.Join(imports, ",") == "" {
		t.Fatal("unexpected empty import inspection")
	}
}

func strconvUnquote(value string) (string, error) {
	var decoded string
	if err := json.Unmarshal([]byte(value), &decoded); err != nil {
		return "", err
	}
	return decoded, nil
}

func TestSemanticsFixtureIsReadOnlyDuringGoConformance(t *testing.T) {
	path := filepath.Join(contractRoot(t), "fixtures", "semantics", "manifest.json")
	before, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	manifest := loadSemanticsManifest(t)
	for _, item := range manifest.Cases {
		ProjectSemantics(item)
	}
	after, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(before, after) {
		t.Fatal("Go semantics conformance mutated the shared fixture")
	}
}
