package join

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestFrozenPythonDetails(t *testing.T) {
	body, err := os.ReadFile("testdata/python_details.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, HTML, Fill string
		Prefix           int
		Config           map[string]json.RawMessage
		Expected         map[string]any
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			got, err := ParseDetail([]byte(strings.Repeat(tc.Fill, tc.Prefix)+tc.HTML), tc.Config)
			raw, _ := json.Marshal(got)
			var actual map[string]any
			_ = json.Unmarshal(raw, &actual)
			if err != nil || !reflect.DeepEqual(actual, tc.Expected) {
				t.Fatalf("got %#v; expected %#v: %v", actual, tc.Expected, err)
			}
		})
	}
}

type detailClient struct {
	body     string
	status   int
	headers  http.Header
	requests int
}

func (c *detailClient) Do(r *http.Request) (*http.Response, error) {
	c.requests++
	return &http.Response{StatusCode: c.status, Body: io.NopCloser(strings.NewReader(c.body)), Request: r, Header: c.headers}, nil
}
func detailConfig(t *testing.T) map[string]json.RawMessage {
	t.Helper()
	var config map[string]json.RawMessage
	_ = json.Unmarshal([]byte(`{"path":"props.pageProps.initialState.job","fields":{"title":"title"}}`), &config)
	return config
}
func TestDetailNoFieldsNoHTTPAndNon200EmptyWithoutRetry(t *testing.T) {
	for _, status := range []int{200, 404, 410, 429, 503} {
		client := &detailClient{status: status, body: `<script id="__NEXT_DATA__">{"props":{"pageProps":{"initialState":{"job":{"title":"Go"}}}}}</script>`}
		request := DetailRequest{URL: "https://join.com/companies/acme/1"}
		result, err := fetchDetail(context.Background(), client, request)
		if err != nil || client.requests != 0 || result.Requests != 0 || result.Content["title"] != nil {
			t.Fatalf("empty config made HTTP: %#v, %v", result, err)
		}
		request.Config = detailConfig(t)
		result, err = fetchDetail(context.Background(), client, request)
		if err != nil || client.requests != 1 || result.Requests != 1 {
			t.Fatalf("detail retried: %#v, %v", result, err)
		}
		if status != 200 && result.Content["title"] != nil {
			t.Fatal("non200 was parsed")
		}
		if status == 200 && result.Content["title"] != "Go" {
			t.Fatalf("lost title: %#v", result)
		}
	}
}
func TestFrozenPythonPublisherMetadata(t *testing.T) {
	body, err := os.ReadFile("testdata/python_policies.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, HTML, Fill string
		Prefix           int
		Headers          map[string]string
		Expected         struct {
			Reserved       bool
			Source, Policy string
		}
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			headers := make(http.Header)
			for k, v := range tc.Headers {
				headers.Set(k, v)
			}
			client := &detailClient{status: 200, body: strings.Repeat(tc.Fill, tc.Prefix) + tc.HTML, headers: headers}
			result, err := fetchDetail(context.Background(), client, DetailRequest{URL: "https://join.com/companies/acme/1", Config: detailConfig(t)})
			if (err != nil) != tc.Expected.Reserved || result.TDMSource != tc.Expected.Source || result.TDMPolicy != tc.Expected.Policy {
				t.Fatalf("policy got %#v %v; want %#v", result.FetchResult, err, tc.Expected)
			}
		})
	}
}
func TestPassiveCaptureBoundModeAndNoOverwrite(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("JOIN_CAPTURE_SLUGS", "acme")
	captureText(dir, "https://join.com/companies/other/1", []byte("no"), true)
	for i := 0; i < 8; i++ {
		captureText(dir, "https://join.com/companies/acme/"+string(rune('a'+i)), []byte("first"), true)
	}
	captureText(dir, "https://join.com/companies/acme/a", []byte("later"), true)
	captureText(dir, "https://join.com/companies/acme?page=1", []byte("page-first"), false)
	captureText(dir, "https://join.com/companies/acme?page=1", []byte("page-later"), false)
	files, err := os.ReadDir(dir)
	if err != nil || len(files) != 5 {
		t.Fatalf("unbounded files %d: %v", len(files), err)
	}
	for _, f := range files {
		info, _ := f.Info()
		if info.Mode().Perm() != 0600 {
			t.Fatal("capture permissions")
		}
	}
	page, _ := os.ReadFile(filepath.Join(dir, "jobseek-join-go-acme-1.html"))
	if string(page) != "page-first" {
		t.Fatal("capture overwritten")
	}
}
func TestUnsupportedDetailRejectedBeforeHTTP(t *testing.T) {
	client := &detailClient{status: 200}
	for _, url := range []string{"https://evil.example/companies/acme/1", "https://join.com/companies/acme", "https://user@join.com/companies/acme/1"} {
		if _, err := fetchDetail(context.Background(), client, DetailRequest{URL: url, Config: detailConfig(t)}); err == nil {
			t.Fatal("unsupported URL admitted")
		}
	}
	config := detailConfig(t)
	config["render"] = json.RawMessage(`true`)
	if _, err := fetchDetail(context.Background(), client, DetailRequest{URL: "https://join.com/companies/acme/1", Config: config}); err == nil || client.requests != 0 {
		t.Fatal("unsupported configuration fetched")
	}
}
