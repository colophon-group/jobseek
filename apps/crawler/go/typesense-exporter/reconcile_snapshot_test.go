package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestReconciliationPayloadPrecisionAndArraySemantics(t *testing.T) {
	decode := func(raw string) map[string]any {
		t.Helper()
		decoder := json.NewDecoder(strings.NewReader(raw))
		decoder.UseNumber()
		var result map[string]any
		if err := decoder.Decode(&result); err != nil {
			t.Fatal(err)
		}
		return result
	}
	for _, test := range []struct {
		name, local, remote string
		equal               bool
	}{
		{"integral float", `{"salary_min":1}`, `{"salary_min":1.0}`, true},
		{"exponent", `{"salary_min":1000000}`, `{"salary_min":1e6}`, true},
		{"same double", `{"salary_min":0.1}`, `{"salary_min":0.10000000000000001}`, true},
		{"large integral double", `{"salary_min":1e30}`, `{"salary_min":1000000000000000019884624838656}`, true},
		{"int64 adjacent", `{"candidate_order_hi":9223372036854775807}`, `{"candidate_order_hi":9223372036854775806}`, false},
		{"signed word", `{"candidate_order_lo":-9223372036854775808}`, `{"candidate_order_lo":-9223372036854775807}`, false},
		{"null and absent", `{}`, `{"company_icon":null}`, true},
		{"unordered arrays", `{"locales":["de","en"],"occupation_ids":[2,1]}`, `{"locales":["en","de"],"occupation_ids":[1.0,2]}`, true},
		{"position matters", `{"location_ids":[1,2]}`, `{"location_ids":[2,1]}`, false},
		{"technology position", `{"technology_names":["Go","Python"]}`, `{"technology_names":["Python","Go"]}`, false},
		{"numeric string", `{"salary_min":1}`, `{"salary_min":"1"}`, false},
		{"unknown ignored", `{"title":"x"}`, `{"title":"x","extra":"y"}`, true},
		{"unicode", `{"title":"A\u2028B"}`, "{\"title\":\"A B\"}", true},
	} {
		t.Run(test.name, func(t *testing.T) {
			local, err := reconciliationFingerprint(decode(test.local))
			if err != nil {
				t.Fatal(err)
			}
			remote, err := reconciliationFingerprint(decode(test.remote))
			if err != nil {
				t.Fatal(err)
			}
			if (local == remote) != test.equal {
				t.Fatalf("equality = %v, want %v", local == remote, test.equal)
			}
		})
	}
	// The production projection starts with native int64, not a JSON number.
	local, err := reconciliationFingerprint(map[string]any{"candidate_order_hi": int64(9223372036854775807)})
	if err != nil {
		t.Fatal(err)
	}
	remote, err := reconciliationFingerprint(decode(`{"candidate_order_hi":9223372036854775807}`))
	if err != nil || local != remote {
		t.Fatalf("native int64 changed: %v", err)
	}
}

func TestReconciliationDiffPreservesAllRepairClasses(t *testing.T) {
	local := reconciliationSnapshot{
		"missing": {Active: true}, "state": {Active: true}, "payload": {Fingerprint: [32]byte{1}}, "both": {Active: true, Fingerprint: [32]byte{1}},
	}
	remote := reconciliationSnapshot{
		"state": {}, "payload": {}, "both": {}, "orphan-active": {Active: true}, "orphan-inactive": {},
	}
	diff := compareReconciliationSnapshots(local, remote)
	if len(diff.Missing) != 1 || len(diff.State) != 2 || len(diff.Payload) != 2 || len(diff.RemoteActive) != 1 || len(diff.RemoteInactive) != 1 || len(diff.candidates()) != 6 {
		t.Fatalf("unexpected diff: %+v", diff)
	}
	if len(local.subset(reconciliationIDs{"missing": {}, "absent": {}})) != 1 || local.active() != 3 {
		t.Fatal("bad subset or active count")
	}
}

func TestReconciliationDocumentValidationAndBounds(t *testing.T) {
	id := "a1000000-0000-0000-0000-000000000001"
	snapshot := reconciliationSnapshot{}
	if err := addReconciliationDocument(snapshot, map[string]any{"id": id, "is_active": true}); err != nil {
		t.Fatal(err)
	}
	for _, doc := range []map[string]any{
		{"id": strings.ReplaceAll(strings.ToUpper(id), "-", ""), "is_active": true},
		{"id": "invalid", "is_active": true},
		{"id": "a2000000-0000-0000-0000-000000000001", "is_active": 1},
		{"is_active": false},
	} {
		if err := addReconciliationDocument(snapshot, doc); err == nil {
			t.Fatalf("accepted invalid/duplicate document: %v", doc)
		}
	}
	for partition := 0; partition < 256; partition++ {
		lower, upper, err := reconciliationBounds(partition)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := reconciliationUUID(lower); err != nil {
			t.Fatal(err)
		}
		if partition == 255 {
			if upper != nil {
				t.Fatal("last partition needs no upper bound")
			}
			continue
		}
		next, _, _ := reconciliationBounds(partition + 1)
		if upper == nil || *upper != next || lower >= next {
			t.Fatal("partition gap or overlap")
		}
	}
	if _, _, err := reconciliationBounds(256); err == nil {
		t.Fatal("accepted out-of-range partition")
	}
}
