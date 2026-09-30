package executor

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"os"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/framing"
	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
	"google.golang.org/protobuf/proto"
)

func protocolFixture(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	body, err := os.ReadFile("testdata/python_protocol.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]json.RawMessage
	if err := json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func TestFrozenPythonProtocol(t *testing.T) {
	fixture := protocolFixture(t)
	request, err := DecodeRequest(fixture["request"])
	if err != nil || request.ClaimToken != "7:11" || request.LeaseUntilMS != 10000 || request.Result.GetSuccess().GetStatus() != 200 {
		t.Fatalf("decode: %+v %v", request, err)
	}
	var cases []struct {
		Message map[string]any `json:"message"`
		Frame   string         `json:"frame_base64"`
	}
	if err := json.Unmarshal(fixture["messages"], &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		expected, err := base64.StdEncoding.DecodeString(c.Frame)
		if err != nil {
			t.Fatal(err)
		}
		var output bytes.Buffer
		if err := WriteMessage(&output, c.Message); err != nil || !bytes.Equal(output.Bytes(), expected) {
			t.Fatalf("Python message mismatch: %v", err)
		}
		payload, err := ReadFrame(bytes.NewReader(expected))
		if err != nil || !json.Valid(payload) {
			t.Fatalf("Python frame rejected: %v", err)
		}
	}
}

func TestRequestRejectsTransportCorruption(t *testing.T) {
	fixture := protocolFixture(t)
	for _, mutate := range []func(map[string]any){
		func(m map[string]any) { m["version"] = "other" },
		func(m map[string]any) { m["unexpected"] = true },
		func(m map[string]any) { delete(m, "claim_token") },
		func(m map[string]any) { m["claim_token"] = nil },
		func(m map[string]any) { m["lease_until_ms"] = true },
		func(m map[string]any) { m["lease_until_ms"] = 0 },
		func(m map[string]any) { m["lease_until_ms"] = maxIdentityInteger + 1 },
		func(m map[string]any) { m["task_payload"] = m["task_payload"].(string) + " " },
		func(m map[string]any) { m["browser_result"] = m["browser_result"].(string) + "=" },
		func(m map[string]any) { m["browser_result"] = m["browser_result"].(string) + "\n" },
		func(m map[string]any) { m["browser_result"] = "" },
	} {
		var request map[string]any
		if err := json.Unmarshal(fixture["request"], &request); err != nil {
			t.Fatal(err)
		}
		mutate(request)
		payload, err := json.Marshal(request)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := DecodeRequest(payload); !errors.Is(err, ErrProtocol) {
			t.Fatalf("accepted corrupted request: %s %v", payload, err)
		}
	}
	for _, payload := range [][]byte{[]byte("null"), []byte("[]"), append(fixture["request"], []byte("{}")...), {0xff}} {
		if _, err := DecodeRequest(payload); err == nil {
			t.Fatal("accepted invalid JSON")
		}
	}
}

func TestResultRejectsUnknownAndNoncanonicalFields(t *testing.T) {
	request, err := DecodeRequest(protocolFixture(t)["request"])
	if err != nil {
		t.Fatal(err)
	}
	valid, err := proto.MarshalOptions{Deterministic: true}.Marshal(request.Result)
	if err != nil {
		t.Fatal(err)
	}
	// Unknown field 99 at the root and inside the success message must both fail.
	rootUnknown := append(append([]byte(nil), valid...), 0x98, 0x06, 0x01)
	nested := proto.Clone(request.Result).(*runtimev1.BrowserResult)
	nested.GetSuccess().ProtoReflect().SetUnknown([]byte{0x98, 0x06, 0x01})
	nestedUnknown, _ := proto.MarshalOptions{Deterministic: true}.Marshal(nested)
	// Repeating a known scalar field is parseable but fails canonical reserialization.
	repeated := append(append([]byte(nil), valid...), 0x10, byte(runtimev1.BrowserBackend_BROWSER_BACKEND_LIGHTPANDA))
	for _, payload := range [][]byte{nil, rootUnknown, nestedUnknown, repeated, {0xff}} {
		if _, err := DecodeResult(payload); err == nil {
			t.Fatal("accepted invalid protobuf")
		}
	}
	for _, mutate := range []func(*runtimev1.BrowserResult){
		func(r *runtimev1.BrowserResult) { r.ContractVersion = "other" },
		func(r *runtimev1.BrowserResult) { r.Backend = runtimev1.BrowserBackend_BROWSER_BACKEND_CHROMIUM },
		func(r *runtimev1.BrowserResult) { r.Outcome = nil },
	} {
		r := proto.Clone(request.Result).(*runtimev1.BrowserResult)
		mutate(r)
		payload, _ := proto.MarshalOptions{Deterministic: true}.Marshal(r)
		if _, err := DecodeResult(payload); err == nil {
			t.Fatal("accepted invalid result identity")
		}
	}
}

func TestAuthorizationAndAttestationIdentity(t *testing.T) {
	valid := []byte(`{"type":"authorized","claim_token":"7:11","lease_until_ms":11000}`)
	if lease, err := DecodeAuthorization(valid, "7:11", 10000); err != nil || lease != 11000 {
		t.Fatalf("authorization: %d %v", lease, err)
	}
	for _, payload := range []string{
		`{"type":"authorized","claim_token":"7:12","lease_until_ms":11000}`,
		`{"type":"authorized","claim_token":"7:11","lease_until_ms":10000}`,
		`{"type":"authorized","claim_token":"7:11","lease_until_ms":true}`,
		`{"type":"authorized","claim_token":"7:11","lease_until_ms":11000,"extra":0}`,
	} {
		if _, err := DecodeAuthorization([]byte(payload), "7:11", 10000); err == nil {
			t.Fatal("accepted changed authority")
		}
	}
	preflight := []byte(`{"version":"jobseek.lightpanda.executor/v1","type":"attest_route","shard_id":"lightpanda-b0","routing_epoch":7}`)
	if err := DecodeAttestation(preflight, "lightpanda-b0", 7); err != nil {
		t.Fatal(err)
	}
	if err := DecodeAttestation(preflight, "lightpanda-b0", 8); err == nil {
		t.Fatal("accepted stale route")
	}
	if err := DecodeAttestation(preflight, "other", 7); err == nil {
		t.Fatal("accepted changed shard")
	}
}

type partialWriter struct{ bytes.Buffer }

func (w *partialWriter) Write(p []byte) (int, error) {
	if len(p) > 3 {
		p = p[:3]
	}
	return w.Buffer.Write(p)
}

type stoppedWriter struct{}

func (stoppedWriter) Write([]byte) (int, error) { return 0, nil }

func TestFramingBoundsAndPartialWrites(t *testing.T) {
	var output partialWriter
	if err := WriteMessage(&output, map[string]any{"type": "error", "error": "executor_failed"}); err != nil {
		t.Fatal(err)
	}
	if _, err := ReadFrame(bytes.NewReader(output.Bytes())); err != nil {
		t.Fatal(err)
	}
	if err := WriteMessage(stoppedWriter{}, map[string]any{"type": "error"}); !errors.Is(err, io.ErrShortWrite) {
		t.Fatalf("short write: %v", err)
	}
	for _, payload := range [][]byte{{0x80, 0x00}, {0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x02}, {0x80, 0x80, 0xc0, 0x01}} {
		if _, err := ReadFrame(bytes.NewReader(payload)); err == nil {
			t.Fatal("accepted bad prefix")
		}
	}
	if _, err := framing.EncodeRecord(make([]byte, FrameLimit), FrameLimit); !errors.Is(err, framing.ErrFrameLimit) {
		t.Fatalf("prefix-inclusive bound: %v", err)
	}
}
