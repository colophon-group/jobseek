package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestSeekDetailActualPythonGraphQLRequestsAndContent(t *testing.T) {
	var cases []struct {
		Name, Source string
		Config       map[string]any
		Payload      json.RawMessage
		Requests     []struct {
			Method, URL string
			Headers     map[string]string
			Body        map[string]any
		}
		Expected map[string]any
		Error    bool
	}
	raw, err := os.ReadFile("testdata/python_seek_detail.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 25 {
		t.Fatal("actual Python detail corpus missing", err, len(cases))
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			o, err := SeekDetailOptionsForSource(c.Source, c.Config)
			if err != nil {
				if !c.Error || len(c.Requests) > 0 {
					t.Fatal("request eligibility differs", err)
				}
				return
			}
			if len(c.Requests) != 1 {
				t.Fatal("request corpus missing")
			}
			want := c.Requests[0]
			var body map[string]any
			if json.Unmarshal([]byte(o.Request.Body), &body) != nil || !reflect.DeepEqual(body, want.Body) || o.Request.URL != want.URL || o.Request.Method != want.Method || len(o.Request.Headers) != len(want.Headers) {
				t.Fatal("GraphQL request differs", o.Request, want)
			}
			for key, value := range want.Headers {
				if o.Request.Headers.Get(key) != value {
					t.Fatal("public request header differs", key)
				}
			}
			d, err := Decode(c.Payload)
			if err != nil {
				t.Fatal(err)
			}
			actual, err := ProjectSeekDetail(d, o)
			if (err != nil) != c.Error {
				t.Fatal("validated content outcome differs", actual, err)
			}
			if c.Error {
				return
			}
			wire, _ := json.Marshal(actual)
			json.Unmarshal(wire, &actual)
			if !reflect.DeepEqual(actual, c.Expected) {
				t.Fatal("content differs", actual, c.Expected)
			}
		})
	}
}

func TestSeekDetailRejectsForeignEndpointsAndTransportOptions(t *testing.T) {
	for _, source := range []string{"http://au.seek.com/job/123", "https://au.seek.com.evil.test/job/123", "https://user@au.seek.com/job/123", "https://au.seek.com:8443/job/123", "https://au.seek.com/jobs?advertiserid=123", "https://au.seek.com/job/not-a-job", "https://au.seek.com/job/%31", "https://au.seek.com/job/123/extra"} {
		if _, err := SeekDetailOptionsForSource(source, nil); err == nil {
			t.Fatal("foreign source admitted", source)
		}
	}
	for _, option := range []string{"api_url", "query", "headers", "proxy", "render", "skip_ssl"} {
		if _, err := SeekDetailOptionsForSource("https://au.seek.com/job/123", map[string]any{option: true}); err == nil {
			t.Fatal("publisher transport override admitted", option)
		}
	}
}
