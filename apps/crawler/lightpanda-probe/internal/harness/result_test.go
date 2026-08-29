package harness

import (
	"bytes"
	"encoding/json"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

func TestMarshalEnvelopeIsDeterministic(t *testing.T) {
	t.Parallel()
	result := Failure(
		runtimev1.ErrorCode_ERROR_CODE_TDM_RESERVED,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		"synthetic response reserves text and data mining",
	)
	resultJSON, err := MarshalResult(result)
	if err != nil {
		t.Fatal(err)
	}
	envelope := Envelope{
		Format:     OutputFormat,
		FixtureID:  "tdm-header",
		PlanSHA256: SHA256([]byte("plan")),
		Result:     resultJSON,
		Ledger: []LedgerEntry{
			{Method: "GET", Path: "/z", ResourceType: "Script", Decision: "allowed"},
			{Method: "GET", Path: "/a", ResourceType: "Document", Decision: "allowed"},
		},
		Cleanup: Cleanup{SessionClosed: true, Outcome: "closed"},
	}
	first, err := MarshalEnvelope(envelope)
	if err != nil {
		t.Fatal(err)
	}
	second, err := MarshalEnvelope(envelope)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(first, second) {
		t.Fatal("deterministic envelope changed")
	}
	if bytes.Index(first, []byte(`"path": "/a"`)) > bytes.Index(first, []byte(`"path": "/z"`)) {
		t.Fatal("ledger was not sorted")
	}
	if !bytes.Contains(first, []byte(`"sequence": 1`)) || !bytes.Contains(first, []byte(`"sequence": 2`)) {
		t.Fatal("ledger sequence was not assigned after deterministic sorting")
	}
}

func TestMarshalEnvelopeNormalizesOnlyCopiedRequestLimitCompletion(t *testing.T) {
	t.Parallel()
	result := Failure(
		runtimev1.ErrorCode_ERROR_CODE_RESOURCE_LIMIT,
		runtimev1.ErrorDisposition_ERROR_DISPOSITION_FAIL_CLOSED_POLICY,
		"synthetic request limit exceeded",
	)
	resultJSON, err := MarshalResult(result)
	if err != nil {
		t.Fatal(err)
	}
	ledger := []LedgerEntry{
		{Method: "GET", Path: "/request-overflow", ResourceType: "Document", Decision: "allowed", Status: 200, ResponseBytes: 266},
		{Method: "GET", Path: "/robots.txt", ResourceType: "Policy", Decision: "allowed", Status: 200, ResponseBytes: 42},
		{Method: "GET", Path: "/static/limit-1.js", ResourceType: "Script", Decision: "allowed"},
		{Method: "GET", Path: "/static/limit-2.js", ResourceType: "Script", Decision: "allowed"},
		{Method: "GET", Path: "/static/limit-3.js", ResourceType: "Script", Decision: "blocked", Reason: "request_limit"},
	}
	a := Envelope{
		Format:        OutputFormat,
		FixtureID:     "request-overflow",
		PlanSHA256:    SHA256([]byte("plan")),
		Result:        resultJSON,
		Ledger:        append([]LedgerEntry(nil), ledger...),
		RequestCount:  5,
		ResponseBytes: 308,
		Cleanup:       Cleanup{SessionClosed: true, TargetsBefore: 1, TargetsAfter: -1, Outcome: "closed"},
	}
	b := a
	b.Ledger = append([]LedgerEntry(nil), ledger...)
	b.Ledger[2].Status = 200
	b.Ledger[2].ResponseBytes = 7
	b.Ledger[3].Status = 200
	b.Ledger[3].ResponseBytes = 7
	b.ResponseBytes = 322

	encodedA, err := MarshalEnvelope(a)
	if err != nil {
		t.Fatal(err)
	}
	encodedB, err := MarshalEnvelope(b)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(encodedA, encodedB) {
		t.Fatalf("request-limit snapshots did not normalize:\nA=%s\nB=%s", encodedA, encodedB)
	}
	if b.Ledger[2].Status != 200 || b.Ledger[2].ResponseBytes != 7 || b.ResponseBytes != 322 {
		t.Fatal("serialization mutated the live request-limit snapshot")
	}

	var got Envelope
	if err := json.Unmarshal(encodedA, &got); err != nil {
		t.Fatal(err)
	}
	if got.RequestCount != 5 || got.ResponseBytes != 308 || len(got.Ledger) != 5 {
		t.Fatalf("request-limit enforcement fields changed: count=%d bytes=%d ledger=%d", got.RequestCount, got.ResponseBytes, len(got.Ledger))
	}
	expectedPaths := []string{"/request-overflow", "/robots.txt", "/static/limit-1.js", "/static/limit-2.js", "/static/limit-3.js"}
	for index, entry := range got.Ledger {
		if entry.Path != expectedPaths[index] || entry.Sequence != uint64(index+1) {
			t.Fatalf("request identity or order changed at %d: %+v", index, entry)
		}
		if entry.Decision == "allowed" && entry.ResourceType == "Script" && (entry.Status != 0 || entry.ResponseBytes != 0) {
			t.Fatalf("racy allowed-subresource completion was retained: %+v", entry)
		}
	}
	blocked := got.Ledger[4]
	if blocked.Path != "/static/limit-3.js" || blocked.Decision != "blocked" || blocked.Reason != "request_limit" {
		t.Fatalf("blocked fifth request changed: %+v", blocked)
	}
	var rawEnvelope struct {
		Ledger []map[string]any `json:"ledger"`
	}
	if err := json.Unmarshal(encodedA, &rawEnvelope); err != nil {
		t.Fatal(err)
	}
	for _, entry := range rawEnvelope.Ledger {
		if entry["decision"] != "allowed" || entry["resource_type"] != "Script" {
			continue
		}
		if _, present := entry["status"]; present {
			t.Fatalf("racy status field was not omitted: %+v", entry)
		}
		if _, present := entry["response_bytes"]; present {
			t.Fatalf("racy response-byte field was not omitted: %+v", entry)
		}
	}
	var gotResult struct {
		Error struct {
			Error struct {
				Code        string `json:"code"`
				Disposition string `json:"disposition"`
			} `json:"error"`
		} `json:"error"`
	}
	if err := json.Unmarshal(got.Result, &gotResult); err != nil {
		t.Fatal(err)
	}
	if gotResult.Error.Error.Code != "ERROR_CODE_RESOURCE_LIMIT" || gotResult.Error.Error.Disposition != "ERROR_DISPOSITION_FAIL_CLOSED_POLICY" {
		t.Fatalf("typed request-limit result changed: %+v", gotResult.Error.Error)
	}
}
