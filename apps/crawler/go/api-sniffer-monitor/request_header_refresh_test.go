package apisniffer

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"strings"
	"testing"
)

func TestPublicRequestHeaderRefreshIsOperationLocal(t *testing.T) {
	metadata := `{"api_url":"https://api.fixture.invalid/jobs","json_path":"jobs","url_field":"url","request_headers":{"rolecode":"manager"},"request_header_refresh":{"source_url":"https://fixture.invalid/public/context","fields":{"myJobsToken":"context.value"}}}`
	o, err := OptionsFromMetadata("https://fixture.invalid/careers", metadata)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []string{"fresh-one", "fresh-two"} {
		calls := 0
		result, err := Discover(context.Background(), o, func(_ context.Context, r Request) (*Document, error) {
			calls++
			if calls < 3 {
				if !o.IsRequestHeaderRefreshRequest(r) || len(r.Headers) != 0 {
					t.Fatal("bootstrap carried inventory context")
				}
				if calls == 1 && r.URL == o.BoardURL {
					return &Document{Value: "public careers"}, nil
				}
				if calls == 2 && r.URL == o.RequestHeaderRefresh.source {
					return &Document{Value: map[string]any{"context": map[string]any{"value": value}}}, nil
				}
				t.Fatal("bootstrap order changed")
			}
			if r.URL != o.Endpoint || r.Headers.Get("myJobsToken") != value || r.Headers.Get("rolecode") != "manager" {
				t.Fatal("exact API context missing")
			}
			return Decode([]byte(`{"jobs":[{"url":"https://fixture.invalid/job/one"}]}`))
		}, func(base, ref string) (string, error) {
			u, _ := url.Parse(base)
			v, _ := url.Parse(ref)
			return u.ResolveReference(v).String(), nil
		})
		if err != nil || result.Truncated || len(result.Jobs) != 1 || calls != 3 {
			t.Fatal("public context did not produce complete inventory")
		}
		if o.Headers.Get("myJobsToken") != "" {
			t.Fatal("refreshed context escaped its operation")
		}
	}
}

func TestPublicRequestHeaderRefreshRejectsMissingAndMalformedContext(t *testing.T) {
	base := map[string]any{"api_url": "https://api.fixture.invalid/jobs", "json_path": "jobs", "request_header_refresh": map[string]any{"source_url": "https://fixture.invalid/context", "fields": map[string]any{"myJobsToken": "context"}}}
	raw, _ := json.Marshal(base)
	o, err := OptionsFromMetadata("https://fixture.invalid/careers", string(raw))
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []any{nil, "", "bad\r\nheader", strings.Repeat("a", 8193), true, map[string]any{"value": "nested"}} {
		_, err := o.refreshedRequestHeaders(&Document{Value: map[string]any{"context": value}})
		if !errors.Is(err, ErrInventory) {
			t.Fatal("malformed public context accepted")
		}
	}
	for _, source := range []string{"https://other.invalid/context", "http://fixture.invalid/context", "https://fixture.invalid/careers", "https://user@fixture.invalid/context"} {
		refresh := base["request_header_refresh"].(map[string]any)
		refresh["source_url"] = source
		raw, _ = json.Marshal(base)
		if _, err := OptionsFromMetadata("https://fixture.invalid/careers", string(raw)); !errors.Is(err, ErrOptions) {
			t.Fatal("unbound context source accepted")
		}
	}
}
