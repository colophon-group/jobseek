package harness

import (
	"bytes"
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
