package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strconv"
	"testing"
)

func TestExistingPythonHTTPDiscoveryOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name      string          `json:"name"`
		BoardURL  string          `json:"board_url"`
		Metadata  json.RawMessage `json:"metadata"`
		Expected  []Job           `json:"expected"`
		Truncated bool            `json:"truncated"`
		Error     bool            `json:"error"`
		Responses []struct {
			Page   int
			Size   *int
			Status int
			Data   json.RawMessage
		}
	}
	if json.Unmarshal(body, &cases) != nil {
		t.Fatal("invalid Python fixture")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := OptionsFromMetadata(c.BoardURL, string(c.Metadata))
			if c.Name == "url-only" {
				if err == nil {
					t.Fatal("Python auto-maps rich fields; native admission must retain its owner until that contract exists")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			fetch := func(ctx context.Context, r Request) (*Document, error) {
				u, err := url.Parse(r.URL)
				if err != nil {
					return nil, err
				}
				q := u.Query()
				page := 0
				size := 0
				var post map[string]any
				if r.Body != "" {
					_ = json.Unmarshal([]byte(r.Body), &post)
				}
				for _, k := range []string{"page", "offset"} {
					if s := q.Get(k); s != "" {
						page, _ = strconv.Atoi(s)
					} else if n, ok := post[k].(float64); ok {
						page = int(n)
					}
				}
				if s := q.Get("limit"); s != "" {
					size, _ = strconv.Atoi(s)
				} else if n, ok := post["limit"].(float64); ok {
					size = int(n)
				}
				for _, response := range c.Responses {
					if response.Page == page && (response.Size == nil || *response.Size == size) {
						if response.Status >= 400 {
							return nil, fmt.Errorf("fixture HTTP %d", response.Status)
						}
						return Decode(response.Data)
					}
				}
				return Decode([]byte(`{"jobs":[]}`))
			}
			join := func(base, ref string) (string, error) {
				b, err := url.Parse(base)
				if err != nil {
					return "", err
				}
				r, err := url.Parse(ref)
				if err != nil {
					return "", err
				}
				return b.ResolveReference(r).String(), nil
			}
			actual, err := Discover(context.Background(), o, fetch, join)
			if c.Error {
				if err == nil {
					t.Fatalf("Python rejects whole discovery; native returned %#v", actual)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if actual.Truncated != c.Truncated {
				t.Fatalf("truncated %v; Python %v", actual.Truncated, c.Truncated)
			}
			if !reflect.DeepEqual(actual.Jobs, c.Expected) {
				a, _ := json.Marshal(actual.Jobs)
				e, _ := json.Marshal(c.Expected)
				t.Fatalf("native %s; Python %s", a, e)
			}
		})
	}
}

func TestResponseAuthorityOnlyIncludesBoundRequests(t *testing.T) {
	o, err := OptionsFromMetadata("https://careers.example.test/", `{"api_url":"https://api.example.test/jobs?limit=10","json_path":"jobs","url_field":"url","fields":{"title":"title"},"pagination":{"param_name":"offset","style":"offset","increment":10}}`)
	if err != nil {
		t.Fatal(err)
	}
	if !o.ResourceMatches(o.Endpoint) {
		t.Fatal("initial URL missing")
	}
	for _, raw := range []string{"https://api.example.test/jobs?limit=100&offset=0", "https://api.example.test/jobs?limit=10&offset=10"} {
		if !o.ResourceMatches(raw) {
			t.Fatal("bound pagination missing")
		}
	}
	for _, raw := range []string{"https://other.example.test/jobs?limit=10", "https://api.example.test/admin?limit=10", "https://api.example.test/jobs?limit=10&extra=1", "https://api.example.test/jobs?limit=1000", "https://api.example.test/jobs?limit=10&offset=-1", "https://api.example.test/jobs?limit=10&offset=1&offset=2"} {
		if o.ResourceMatches(raw) {
			t.Fatal("unbound request admitted")
		}
	}
}

func TestUnsupportedOptionsRetainExistingOwner(t *testing.T) {
	base := map[string]any{"api_url": "https://api.example.test/jobs", "json_path": "jobs", "url_field": "url", "fields": map[string]any{"title": "title"}}
	keys := []string{"browser", "proxy", "api_url_match", "post_data_refresh", "response_decrypt", "item_filter", "pagination_convergence", "slug_fields", "unknown"}
	sort.Strings(keys)
	for _, key := range keys {
		t.Run(key, func(t *testing.T) {
			m := map[string]any{}
			for k, v := range base {
				m[k] = v
			}
			m[key] = true
			body, _ := json.Marshal(m)
			if _, err := OptionsFromMetadata("https://careers.example.test/", string(body)); err == nil {
				t.Fatal("unsupported behavior ignored")
			}
		})
	}
}
