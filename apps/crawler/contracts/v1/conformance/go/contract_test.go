package conformance

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"testing"

	runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"
)

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

func TestSharedCanonicalURLVectors(t *testing.T) {
	var cases []struct {
		Name  string `json:"name"`
		URL   string `json:"url"`
		Valid bool   `json:"valid"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "url", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, value := range cases {
		err := validURL(value.URL, value.Name)
		if value.Valid && err != nil {
			t.Errorf("%s: valid URL rejected: %v", value.Name, err)
		}
		if !value.Valid && err == nil {
			t.Errorf("%s: invalid URL accepted", value.Name)
		}
	}
}

func TestEveryErrorCodeHasASharedPolicyVector(t *testing.T) {
	var cases []struct {
		Code        string  `json:"code"`
		Disposition string  `json:"disposition"`
		HTTPStatus  *uint32 `json:"http_status"`
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "fixtures", "errors", "cases.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != len(runtimev1.ErrorCode_name)-1 {
		t.Fatalf("got %d error vectors, want %d", len(cases), len(runtimev1.ErrorCode_name)-1)
	}
	for _, value := range cases {
		errorValue := &runtimev1.RuntimeError{
			Code:        runtimev1.ErrorCode(runtimev1.ErrorCode_value[value.Code]),
			Disposition: runtimev1.ErrorDisposition(runtimev1.ErrorDisposition_value[value.Disposition]),
			Message:     "shared typed error vector",
			HttpStatus:  value.HTTPStatus,
		}
		if err := ValidateError(errorValue); err != nil {
			t.Errorf("%s: %v", value.Code, err)
		}
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
	projection := ProjectFrames(replay.GetExpectedFrames(), replay.GetExecutionRequest())
	if projection.GetFilteredCount() != 2 || projection.GetSecurityFilteredCount() != 1 {
		t.Fatalf("projection drift: filtered=%d security_filtered=%d", projection.GetFilteredCount(), projection.GetSecurityFilteredCount())
	}
	if projection.GetGoneDetectionAllowed() {
		t.Fatal("security-filtered output must suppress gone detection")
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

func TestDynamicOriginIsDeclaredBeforeAmbiguousDispatch(t *testing.T) {
	path := filepath.Join("..", "..", "fixtures", "conformance", "positive", "disconnect-after-dynamic-dispatch-before-contact.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	value, err := LoadCase(raw)
	if err != nil {
		t.Fatal(err)
	}
	var declaration *runtimev1.OriginOperationRef
	var faultID string
	var contact *runtimev1.OriginContact
	declarationIndex, faultIndex, contactIndex := -1, -1, -1
	for index, event := range value.GetTranscript().GetEvents() {
		if frame := event.GetServer().GetFrame(); frame != nil {
			if declared := frame.GetOriginOperationDeclared(); declared != nil {
				declaration = declared.GetOperation()
				declarationIndex = index
			}
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
	if declaration == nil || contact == nil || !(declarationIndex < faultIndex && faultIndex < contactIndex) {
		t.Fatal("dynamic declaration, dispatch fault, and deduplicated contact are not ordered")
	}
	requestID := value.GetTranscript().GetEvents()[2].GetClient().GetStart().GetOriginRequestId()
	if declaration.GetOperationSequence() != 1 || declaration.GetParentOriginRequestId() != requestID || faultID != declaration.GetOriginRequestId() {
		t.Fatal("dynamic operation lost its stable sequence, parent, or ID")
	}
	if !bytes.Equal(deterministicBytes(contact.GetOperation()), deterministicBytes(declaration)) || contact.GetDisposition() != runtimev1.OriginContactDisposition_ORIGIN_CONTACT_DISPOSITION_DEDUPLICATED {
		t.Fatal("resumed origin contact does not deduplicate the declared dynamic operation")
	}
}
