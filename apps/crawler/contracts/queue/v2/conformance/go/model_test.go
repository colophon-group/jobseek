package queuev2

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"os"
	"strings"
	"testing"
)

const corpusPath = "../../fixtures/scenarios.json"

func loadCorpus(t *testing.T) ([]byte, Corpus) {
	t.Helper()
	data, err := os.ReadFile(corpusPath)
	if err != nil {
		t.Fatal(err)
	}
	corpus, err := DecodeCorpus(data)
	if err != nil {
		t.Fatal(err)
	}
	return data, corpus
}

func caseByID(t *testing.T, corpus Corpus, id string) Case {
	t.Helper()
	for _, testCase := range corpus.Cases {
		if testCase.ID == id {
			return testCase
		}
	}
	t.Fatalf("missing case %q", id)
	return Case{}
}

func TestGoMatchesEveryCheckedInPythonReferenceCase(t *testing.T) {
	_, corpus := loadCorpus(t)
	for _, testCase := range corpus.Cases {
		t.Run(testCase.ID, func(t *testing.T) {
			result, err := RunCase(testCase)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := CanonicalBytes(result)
			if err != nil {
				t.Fatal(err)
			}
			expected, err := CanonicalBytes(testCase.Expected)
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(actual, expected) {
				t.Fatalf("canonical result differs\nactual:   %s\nexpected: %s", actual, expected)
			}
			resultDigest, err := Digest(result)
			if err != nil {
				t.Fatal(err)
			}
			if resultDigest != testCase.ResultDigest {
				t.Fatalf("digest %s != %s", resultDigest, testCase.ResultDigest)
			}
		})
	}
}

func TestFullCorpusDigestMatchesSidecar(t *testing.T) {
	data, _ := loadCorpus(t)
	digestFile, err := os.ReadFile("../../fixtures/scenarios.sha256")
	if err != nil {
		t.Fatal(err)
	}
	fields := strings.Fields(string(digestFile))
	if len(fields) != 2 || fields[1] != "scenarios.json" {
		t.Fatalf("invalid digest sidecar: %q", digestFile)
	}
	sum := sha256.Sum256(data)
	if got := hex.EncodeToString(sum[:]); got != fields[0] {
		t.Fatalf("corpus digest %s != %s", got, fields[0])
	}
}

func TestReclaimFencesEveryOldClaimTransitionWithoutMutation(t *testing.T) {
	_, corpus := loadCorpus(t)
	testCase := caseByID(t, corpus, "reap_reclaim_fences_every_stale_transition")
	result, err := RunCase(testCase)
	if err != nil {
		t.Fatal(err)
	}
	currentClaimDigest := result.Trace[2].SnapshotDigest
	wantKinds := []string{"heartbeat", "authorize_write", "complete", "reschedule", "fail", "reap"}
	for offset, kind := range wantKinds {
		entry := result.Trace[offset+3]
		if entry.Kind != kind || entry.Decision != "fenced" || entry.Reason != "claim_mismatch" {
			t.Fatalf("unexpected stale transition outcome: %+v", entry)
		}
		if entry.SnapshotDigest != currentClaimDigest {
			t.Fatalf("stale %s mutated state", kind)
		}
		if entry.WriteAuthorized {
			t.Fatalf("stale %s authorized a write", kind)
		}
	}
	if result.Final.Records[0].Failures != 1 || result.Final.Records[0].State != "terminal" {
		t.Fatalf("unexpected final record: %+v", result.Final.Records[0])
	}
}

func TestStaleFailureCannotConsumeNewClaimBudget(t *testing.T) {
	_, corpus := loadCorpus(t)
	testCase := caseByID(t, corpus, "stale_failure_cannot_consume_budget")
	result, err := RunCase(testCase)
	if err != nil {
		t.Fatal(err)
	}
	if result.Trace[3].Decision != "fenced" ||
		result.Trace[3].SnapshotDigest != result.Trace[2].SnapshotDigest {
		t.Fatalf("stale failure was not a no-op: %+v", result.Trace[3])
	}
	final := result.Final.Records[0]
	if final.Failures != 2 || final.State != "dead_letter" {
		t.Fatalf("unexpected final failure budget: %+v", final)
	}
}

func TestStrictDecoderRejectsUnknownField(t *testing.T) {
	data := []byte(`{"cases":[],"format":"jobseek.queue.v2.conformance/v1","future":true}`)
	if _, err := DecodeCorpus(data); err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected unknown-field rejection, got %v", err)
	}
}
