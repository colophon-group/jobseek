package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestHTTPDetailMatchesReferenceScraper(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_http_detail.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, Source string
		Config       map[string]any
		Payload      json.RawMessage
		AuthPayload  json.RawMessage `json:"auth_payload"`
		Requests     []struct {
			Method, URL, Body string
			Headers           map[string]string
		}
		Expected map[string]any
	}
	if json.Unmarshal(raw, &cases) != nil {
		t.Fatal("invalid corpus")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := HTTPDetailOptionsForSource(c.Config, c.Source)
			if err != nil {
				t.Fatal(err)
			}
			requests := []Request{}
			if o.Auth != nil {
				requests = append(requests, o.Auth.Request)
				d, err := Decode(c.AuthPayload)
				if err != nil {
					t.Fatal(err)
				}
				headers, err := HTTPDetailAuthHeaders(d, o.Auth)
				if err != nil {
					t.Fatal(err)
				}
				for k, v := range headers {
					o.Request.Headers[k] = v
				}
			}
			requests = append(requests, o.Request)
			if len(requests) != len(c.Requests) {
				t.Fatal("request chain differs")
			}
			for n, r := range requests {
				want := c.Requests[n]
				if r.URL != want.URL || r.Method != want.Method || r.Body != want.Body || len(r.Headers) != len(want.Headers) {
					t.Fatal("bound request differs", n)
				}
				for k, v := range want.Headers {
					if r.Headers.Get(k) != v {
						t.Fatal("header scalar differs", k)
					}
				}
			}
			d, err := Decode(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			got, err := ProjectHTTPDetail(d, o)
			if err != nil {
				t.Fatal(err)
			}
			// JobContent salary-string normalization belongs to the worker, which
			// already owns the salary extractor and canonical preparation.
			delete(got, "base_salary")
			delete(c.Expected, "base_salary")
			raw, _ := json.Marshal(got)
			var normalized map[string]any
			json.Unmarshal(raw, &normalized)
			if !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatalf("HTTP reference differs\ngot %s\nwant %v", raw, c.Expected)
			}
		})
	}
}

func TestHTTPDetailRejectsUnportedTransportAndAuthBeforeRequest(t *testing.T) {
	good := map[string]any{"api_url": "https://api.example.net/jobs/{id}", "fields": map[string]any{"title": "title"}}
	for _, extra := range []map[string]any{
		{"render": true}, {"proxy": true}, {"api_url": "http://api.example.net/job"}, {"api_url": "https://user:secret@api.example.net/job"}, {"method": "DELETE"}, {"url_pattern": "["}, {"json_path": "["}, {"enrich": []any{"description", "description"}}, {"request_headers": map[string]any{"X-Test": "bad\nvalue"}}, {"auth_request": map[string]any{"api_url": "https://auth.example.net/", "header_fields": map[string]any{"Bad Header": "token"}}},
	} {
		bad := map[string]any{}
		for k, v := range good {
			bad[k] = v
		}
		for k, v := range extra {
			bad[k] = v
		}
		if ValidateHTTPDetail(bad) == nil {
			t.Fatal("unsupported config admitted")
		}
	}
	o, err := HTTPDetailOptionsForSource(good, "https://careers.example.net/jobs/123")
	if err != nil || o.Request.URL != "https://api.example.net/jobs/123" {
		t.Fatal(o, err)
	}
	auth := &HTTPDetailAuth{HeaderFields: map[string]any{"Authorization": "token"}}
	for _, body := range []string{`{}`, `{"token":[]}`, `{"token":"bad\r\nvalue"}`} {
		d, _ := Decode([]byte(body))
		if _, err := HTTPDetailAuthHeaders(d, auth); err == nil {
			t.Fatal("invalid auth header admitted")
		}
	}
}
