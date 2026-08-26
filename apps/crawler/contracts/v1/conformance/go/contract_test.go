package conformance

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

type framingVectorFile struct {
	Vectors []struct {
		Name          string  `json:"name"`
		PayloadHex    *string `json:"payload_hex"`
		PayloadByte   string  `json:"payload_byte"`
		PayloadLength int     `json:"payload_length"`
		Maximum       uint64  `json:"maximum"`
		WireHex       string  `json:"wire_hex"`
		WirePrefixHex string  `json:"wire_prefix_hex"`
		ExpectedError string  `json:"expected_error"`
	} `json:"vectors"`
}

type offlineTransport struct{}

func (offlineTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("offline replay attempted network access")
}

func fixtureFiles(t *testing.T, parts ...string) []string {
	t.Helper()
	root := append([]string{"..", "..", "fixtures"}, parts...)
	files, err := filepath.Glob(filepath.Join(root...) + string(filepath.Separator) + "*.json")
	if err != nil {
		t.Fatal(err)
	}
	sort.Strings(files)
	if len(files) == 0 {
		t.Fatalf("no fixtures under %v", root)
	}
	return files
}

func TestSharedPositiveConformance(t *testing.T) {
	for _, path := range fixtureFiles(t, "conformance", "positive") {
		path := path
		t.Run(filepath.Base(path), func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			value, err := LoadCase(raw)
			if err != nil {
				t.Fatalf("strict protobuf-JSON parse: %v", err)
			}
			if !value.GetExpectedValid() {
				t.Fatal("positive fixture is not marked valid")
			}
			if err := ValidateCase(value); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestSharedNegativeConformance(t *testing.T) {
	for _, path := range fixtureFiles(t, "conformance", "negative") {
		path := path
		t.Run(filepath.Base(path), func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			value, parseErr := LoadCase(raw)
			if filepath.Base(path) == "browser-union-partial-output.json" {
				if parseErr == nil {
					t.Fatal("protobuf oneof accepted success output beside unsupported")
				}
				return
			}
			if parseErr != nil {
				t.Fatalf("strict protobuf-JSON parse: %v", parseErr)
			}
			if value.GetExpectedValid() {
				t.Fatal("negative fixture is marked valid")
			}
			err = ValidateCase(value)
			var violation *Violation
			if !errors.As(err, &violation) {
				t.Fatalf("expected typed violation, got %v", err)
			}
			if violation.Code != value.GetExpectedError() {
				t.Fatalf("got error %s, want %s", violation.Code, value.GetExpectedError())
			}
		})
	}
}

func TestSharedReplayIsOffline(t *testing.T) {
	previous := http.DefaultTransport
	http.DefaultTransport = offlineTransport{}
	t.Cleanup(func() { http.DefaultTransport = previous })
	for _, path := range fixtureFiles(t, "replay") {
		path := path
		t.Run(filepath.Base(path), func(t *testing.T) {
			raw, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			value, err := LoadReplay(raw)
			if err != nil {
				t.Fatal(err)
			}
			if err := ValidateReplay(value, nil); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestRedactionVector(t *testing.T) {
	const expected = "redacted-sha256:3742f7ab8e2513ce9d6da7e6e16d9f1b9797765fd79e11723769b2026fa70e2d"
	if got := Redact("header:authorization", "fixture-token"); got != expected {
		t.Fatalf("redaction drift: got %s", got)
	}
}

func TestNonzeroProjectionAndSemanticHashAreExact(t *testing.T) {
	path := filepath.Join("..", "..", "fixtures", "replay", "representative-paginated-monitor.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	replay, err := LoadReplay(raw)
	if err != nil {
		t.Fatal(err)
	}
	projection, err := ProjectFrames(replay.GetExpectedFrames(), replay.GetExecutionRequest())
	if err != nil {
		t.Fatal(err)
	}
	if projection.GetFilteredCount() != 2 || projection.GetSecurityFilteredCount() != 1 {
		t.Fatalf("projection drift: filtered=%d security_filtered=%d", projection.GetFilteredCount(), projection.GetSecurityFilteredCount())
	}
	left, _ := canonicalJSON(projection)
	right, _ := canonicalJSON(replay.GetExpectedProjection())
	if string(left) != string(right) {
		t.Fatalf("projection differs: got %s want %s", left, right)
	}
	semantic, err := SemanticHash(replay.GetExpectedFrames(), projection)
	if err != nil {
		t.Fatal(err)
	}
	if semantic != replay.GetExpectedSemanticSha256() {
		t.Fatalf("semantic hash differs: got %s want %s", semantic, replay.GetExpectedSemanticSha256())
	}
}

func TestLiveCallerFenceMustMatchRequest(t *testing.T) {
	path := filepath.Join("..", "..", "fixtures", "conformance", "positive", "artifact-handle.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	value, err := LoadCase(raw)
	if err != nil {
		t.Fatal(err)
	}
	request := value.GetTranscript().GetEvents()[2].GetClient().GetStart()
	live := *request.GetFencingContext()
	live.RoutingEpoch--
	live.FenceDigest = FencingDigest(&live)
	err = ValidateTranscript(value.GetTranscript(), &live)
	var violation *Violation
	if !errors.As(err, &violation) || violation.Code != "fence" {
		t.Fatalf("expected stale live fence violation, got %v", err)
	}
}

func TestOriginIsPredeclaredAndFingerprintBoundBeforeAmbiguousDispatch(t *testing.T) {
	path := filepath.Join("..", "..", "fixtures", "conformance", "positive", "disconnect-after-predeclared-dispatch.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	value, err := LoadCase(raw)
	if err != nil {
		t.Fatal(err)
	}
	declaration := value.GetTranscript().GetEvents()[2].GetClient().GetStart().GetOriginOperations()[1]
	var faultID string
	var contact *runtimev1.OriginContact
	faultIndex, contactIndex := -1, -1
	for index, event := range value.GetTranscript().GetEvents() {
		if frame := event.GetServer().GetFrame(); frame != nil {
			if origin := frame.GetOriginContact(); origin != nil && faultIndex >= 0 {
				contact = origin
				contactIndex = index
				break
			}
		}
		if fault := event.GetFault(); fault != nil {
			faultID = fault.GetOriginRequestId()
			faultIndex = index
		}
	}
	if declaration == nil || contact == nil || !(2 < faultIndex && faultIndex < contactIndex) {
		t.Fatal("predeclared operation, dispatch fault, and deduplicated contact are not ordered")
	}
	requestID := value.GetTranscript().GetEvents()[2].GetClient().GetStart().GetOriginRequestId()
	if declaration.GetOperationSequence() != 1 || declaration.GetParentOriginRequestId() != requestID || faultID != declaration.GetOriginRequestId() {
		t.Fatal("operation lost its stable sequence, parent, or ID")
	}
	fault := value.GetTranscript().GetEvents()[faultIndex].GetFault()
	if fault.GetRequestFingerprint() != declaration.GetRequestFingerprint() || contact.GetRequestFingerprint() != declaration.GetRequestFingerprint() {
		t.Fatal("operation request fingerprint changed across disconnect/resume")
	}
	if !bytes.Equal(deterministicBytes(contact.GetOperation()), deterministicBytes(declaration)) || contact.GetDisposition() != runtimev1.OriginContactDisposition_ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED {
		t.Fatal("resumed origin contact does not deduplicate the predeclared operation")
	}
}

func TestSharedBoundedVarintFramingVectors(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "wire", "framing-vectors.json"))
	if err != nil {
		t.Fatal(err)
	}
	var file framingVectorFile
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	for _, vector := range file.Vectors {
		vector := vector
		t.Run(vector.Name, func(t *testing.T) {
			if vector.PayloadHex != nil || vector.PayloadByte != "" {
				var payload []byte
				if vector.PayloadHex != nil {
					payload, err = hex.DecodeString(*vector.PayloadHex)
				} else {
					unit, decodeErr := hex.DecodeString(vector.PayloadByte)
					if decodeErr != nil {
						t.Fatal(decodeErr)
					}
					payload = bytes.Repeat(unit, vector.PayloadLength)
				}
				if err != nil {
					t.Fatal(err)
				}
				wire, encodeErr := EncodeRecord(payload, vector.Maximum)
				if encodeErr != nil {
					t.Fatal(encodeErr)
				}
				prefixHex := vector.WirePrefixHex
				if prefixHex == "" {
					prefixHex = vector.WireHex
				}
				prefix, _ := hex.DecodeString(prefixHex)
				if !bytes.HasPrefix(wire, prefix) || uint64(len(wire)) != uint64(len(payload))+UvarintSize(uint64(len(payload))) {
					t.Fatal("wire prefix/size mismatch")
				}
				decoded, decodeErr := DecodeRecord(wire, vector.Maximum)
				if decodeErr != nil || !bytes.Equal(decoded, payload) {
					t.Fatalf("decode mismatch: %v", decodeErr)
				}
				return
			}
			wire, _ := hex.DecodeString(vector.WireHex)
			_, decodeErr := DecodeRecord(wire, vector.Maximum)
			var violation *Violation
			if !errors.As(decodeErr, &violation) || violation.Code != vector.ExpectedError {
				t.Fatalf("got %v, want %s", decodeErr, vector.ExpectedError)
			}
		})
	}
}

func TestFramingBoundaryUsesExactVarintSize(t *testing.T) {
	payload := bytes.Repeat([]byte("x"), 127)
	if wire, err := EncodeRecord(payload, 128); err != nil || len(wire) != 128 {
		t.Fatalf("exact boundary rejected: %v", err)
	}
	if _, err := EncodeRecord(payload, 127); err == nil {
		t.Fatal("oversize boundary accepted")
	}
}

func stringPointer(value string) *string { return &value }

func TestContentHashCanonicalizesUnorderedRepeatedFields(t *testing.T) {
	left := &runtimev1.JobContent{
		Title: stringPointer("Engineer"), DescriptionHtml: stringPointer("<p>Build</p>"),
		Locations: &runtimev1.StringList{Values: []string{"Zurich", "Bern"}},
		Localizations: []*runtimev1.LocalizedJobContent{
			{Locale: "fr", Title: stringPointer("Ingénieur")},
			{Locale: "de", Title: stringPointer("Ingenieur")},
		},
		Skills: []string{"Python", "Go"},
	}
	right := &runtimev1.JobContent{
		Title: stringPointer("Engineer"), DescriptionHtml: stringPointer("<p>Build</p>"),
		Locations:     &runtimev1.StringList{Values: []string{"Bern", "Zurich"}},
		Localizations: []*runtimev1.LocalizedJobContent{left.Localizations[1], left.Localizations[0]},
		Skills:        []string{"Go", "Python"},
	}
	if ContentHash(left) != ContentHash(right) {
		t.Fatal("unordered repeated fields changed content hash")
	}
	right.Locations = nil
	if ContentHash(left) == ContentHash(right) {
		t.Fatal("optional presence disappeared from content hash")
	}
}

func TestScrapeProjectionBindsSourceURLToContentHash(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "replay", "representative-scrape.json"))
	if err != nil {
		t.Fatal(err)
	}
	replay, err := LoadReplay(raw)
	if err != nil {
		t.Fatal(err)
	}
	projection, err := ProjectFrames(replay.GetExpectedFrames(), replay.GetExecutionRequest())
	if err != nil {
		t.Fatal(err)
	}
	if len(projection.GetJobEffects()) != 1 || projection.GetJobEffects()[0].GetSourceUrl() != replay.GetExecutionRequest().GetScrape().GetSourceUrl() || projection.GetJobEffects()[0].GetContentSha256() != projection.GetContentHashes()[0] {
		t.Fatal("scrape projection lost typed URL/content binding")
	}
}
