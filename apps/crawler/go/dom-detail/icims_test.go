package dom

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestICIMSMatchesPythonMonitorInventoryAndRequests(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_icims.json")
	if err != nil {
		t.Fatal(err)
	}
	type request struct {
		URL             string `json:"url"`
		JSON            bool   `json:"json"`
		FollowRedirects bool   `json:"follow_redirects"`
	}
	var cases []struct {
		Name      string
		BoardURL  string `json:"board_url"`
		Metadata  Object
		Responses map[string]json.RawMessage
		Requests  []request
		Expected  struct {
			URLs             []string
			Truncated, Error bool
		}
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("invalid frozen corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			options, err := ICIMSOptionsFromMetadata(c.BoardURL, c.Metadata)
			if err != nil {
				t.Fatal(err)
			}
			requests := []request{}
			result, err := DiscoverICIMS(context.Background(), options, func(ctx context.Context, r ICIMSRequest) ([]byte, error) {
				requests = append(requests, request{r.URL, r.JSON, r.FollowRedirects})
				raw, ok := c.Responses[r.URL]
				if !ok {
					t.Fatal("unrecorded request", r.URL)
				}
				if r.JSON {
					return raw, nil
				}
				var source string
				if json.Unmarshal(raw, &source) != nil {
					t.Fatal("invalid HTML fixture")
				}
				return []byte(source), nil
			})
			if !reflect.DeepEqual(requests, c.Requests) {
				t.Fatal("iCIMS request chain differs", requests, c.Requests)
			}
			if c.Expected.Error {
				if err == nil {
					t.Fatal("Python rejected inventory but Go accepted")
				}
				return
			}
			if err != nil || !reflect.DeepEqual(result.URLs, c.Expected.URLs) || result.Truncated != c.Expected.Truncated {
				t.Fatal("Python inventory differs", result, c.Expected, err)
			}
		})
	}
}

func TestICIMSRejectsUntrustedHostAndPeerOptions(t *testing.T) {
	for _, metadata := range []Object{
		{"host": "api.icims.com"},
		{"job_hosts": []any{}},
		{"job_hosts": []any{"careers-native.icims.com", "careers-native.icims.com"}},
		{"job_hosts": []any{"evil.example.net"}},
		{"dedupe_job_ids_from_hosts": []any{"careers-native.icims.com"}},
		{"jibe_url": "http://careers.example.net/jobs", "jibe_job_hosts": []any{"careers-native.icims.com"}},
		{"jibe_url": "https://user:secret@careers.example.net/jobs", "jibe_job_hosts": []any{"careers-native.icims.com"}},
		{"jibe_url": "https://careers.example.net/jobs", "jibe_job_hosts": []any{"careers-peer.icims.com"}},
		{"cross_locale_dedupe": map[string]any{"peer_host": "careers-peer.icims.com", "unknown": true}},
		{"cross_locale_dedupe": map[string]any{"peer_host": "careers-peer.icims.com", "title_aliases": map[string]any{" Straße ": "Engineer", "STRASSE": "Developer"}}},
	} {
		board := "https://careers-native.icims.com/jobs/search"
		if metadata["host"] == "api.icims.com" {
			board = "https://example.net/"
		}
		if _, err := ICIMSOptionsFromMetadata(board, metadata); err == nil {
			t.Fatal("untrusted options accepted")
		}
	}
	for _, source := range []string{"https://user:secret@careers-native.icims.com/jobs/123/job", "https://careers-native.icims.com:444/jobs/123/job", "https://evil.example.net/jobs/123/job", "https://careers-native.icims.com/jobs/not-numeric/job"} {
		if ICIMSCanonicalJobURL(source, []string{"careers-native.icims.com"}) != "" {
			t.Fatal("untrusted detail URL accepted")
		}
	}
}
