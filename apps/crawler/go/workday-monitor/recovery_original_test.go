package workday

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"reflect"
	"strings"
	"testing"
)

func TestRecoveryMatchesOriginalPython(t *testing.T) {
	var cases []struct {
		Name, Source   string
		Search, Detail json.RawMessage
		Expected       map[string]any
		Requests       []struct{ Method, URL string }
	}
	body, err := os.ReadFile("testdata/python_recovery.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(body, &cases); err != nil || len(cases) != 8 {
		t.Fatal("missing original fixtures", err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			calls := 0
			client := &http.Client{Transport: recoveryRoundTrip(func(r *http.Request) (*http.Response, error) {
				if calls >= len(c.Requests) || r.Method != c.Requests[calls].Method || r.URL.String() != c.Requests[calls].URL {
					t.Fatal("request order differs from original")
				}
				calls++
				body := c.Search
				if r.Method == "GET" {
					body = c.Detail
				}
				return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"application/json"}}, Body: io.NopCloser(strings.NewReader(string(body))), Request: r}, nil
			})}
			result, err := RecoverPhenomDetailWithClient(context.Background(), c.Source, RecoveryConfig{"fixture", "wd5", "External", false}, client)
			if err != nil || calls != len(c.Requests) || result.Found != (c.Expected != nil) {
				t.Fatal("original recovery outcome differs", err)
			}
			if !result.Found {
				return
			}
			encoded, _ := json.Marshal(result.Detail.Content)
			var actual map[string]any
			if err = json.Unmarshal(encoded, &actual); err != nil {
				t.Fatal(err)
			}
			for key, expected := range c.Expected {
				if !reflect.DeepEqual(actual[key], expected) {
					t.Fatalf("original field %s differs: got %#v expected %#v", key, actual[key], expected)
				}
			}
		})
	}
}
