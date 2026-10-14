package main

import (
	"bytes"
	"os"
	"os/exec"
	"strings"
	"testing"
)

func assertOriginalB0DigestExtractor(t *testing.T, reply []byte, name, want string) {
	t.Helper()
	body, err := os.ReadFile("../../scripts/lightpanda-b0-cutover.sh")
	if err != nil {
		t.Fatal(err)
	}
	start := strings.Index(string(body), name+"() {")
	if start < 0 {
		t.Fatal("original digest extractor missing")
	}
	end := strings.Index(string(body[start:]), "\n}\n")
	if end < 0 {
		t.Fatal("original digest extractor unterminated")
	}
	// Source only the actual extractor, never the mutation-bearing wrapper.
	command := exec.Command("bash", "-c", "set -euo pipefail\n"+string(body[start:start+end+3])+"\nreply=$(cat)\n"+name+" \"$reply\"")
	command.Stdin = bytes.NewReader(reply)
	got, err := command.CombinedOutput()
	if err != nil || string(got) != want+"\n" {
		t.Fatalf("original %s rejected native reply: %s (%v)", name, got, err)
	}
}

func TestB0OperatorReplyPreservesOriginalPythonWire(t *testing.T) {
	digest := strings.Repeat("a", 64)
	rollback := strings.Repeat("b", 64)
	var output bytes.Buffer
	err := writeB0OperatorReply(&output, map[string]any{
		"digest": digest, "rollback_plan_digest": rollback,
		"plan": map[string]any{"text": "https://example.test/a,b:é/\"quoted\"\\tail", "nested": []any{true, nil, 3}},
	})
	want := `{"digest": "` + digest + `", "plan": {"nested": [true, null, 3], "text": "https://example.test/a,b:\u00e9/\"quoted\"\\tail"}, "rollback_plan_digest": "` + rollback + `"}` + "\n"
	if err != nil || output.String() != want {
		t.Fatalf("Python reply wire differs: %q (%v)", output.String(), err)
	}
	assertOriginalB0DigestExtractor(t, output.Bytes(), "extract_digest", digest)
	assertOriginalB0DigestExtractor(t, output.Bytes(), "extract_rollback_digest", rollback)
}
