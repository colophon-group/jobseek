package executor

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"testing"
)

func TestRenderedHTMLMatchesPythonOracle(t *testing.T) {
	data, err := os.ReadFile("testdata/python_rendered.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name    string
			Result  string `json:"result_base64"`
			Outcome string
			HTML    *string
			Status  *uint32
		}
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			payload, err := base64.StdEncoding.DecodeString(c.Result)
			if err != nil {
				t.Fatal(err)
			}
			result, err := DecodeResult(payload)
			if err != nil {
				t.Fatal(err)
			}
			html, err := RenderedHTML(result, "https://example.test/jobs/1")
			switch c.Outcome {
			case "success":
				if err != nil || c.HTML == nil || html != *c.HTML {
					t.Fatalf("HTML mismatch: %v", err)
				}
			case "http":
				var status *NavigationHTTPError
				if !errors.As(err, &status) || c.Status == nil || status.Status != *c.Status || status.RequestedURL != "https://example.test/jobs/1" || status.ResponseURL != result.GetSuccess().FinalUrl || status.PermanentGone() != (*c.Status == 404 || *c.Status == 410) {
					t.Fatalf("HTTP classification mismatch: %v", err)
				}
			case "rejected":
				if !errors.Is(err, ErrRenderedResult) {
					t.Fatalf("accepted invalid manifest: %v", err)
				}
			default:
				t.Fatal("unknown oracle outcome")
			}
		})
	}
}

func TestRenderedURLRejectsInvalidAuthority(t *testing.T) {
	for _, value := range []string{"", "file:///tmp/body", "https://user:pass@example.test/job", "https://example.test/job#fragment", "https://example.test:65536/job", "https://example.test/a\nb", "https://example.test/a b", "https://example.test/%qq", "https://"} {
		if validRenderedURL(value) {
			t.Fatalf("accepted invalid final URL %q", value)
		}
	}
	for _, value := range []string{"https://example.test/job", "http://example.test:8080/job?q=x", "https://[::1]/job"} {
		if !validRenderedURL(value) {
			t.Fatalf("rejected valid final URL %q", value)
		}
	}
}
