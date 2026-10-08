package apisniffer

import (
	"context"
	"testing"
)

func TestAutomaticAPIPathCompleteAndProvedEmptyInventories(t *testing.T) {
	for _, c := range []struct {
		name, payload, extra string
		count                int
		failed               bool
	}{
		{"root", `[{"title":"A","url":"https://example.com/jobs/1"},{"title":"B","url":"https://example.com/jobs/2"},{"title":"C","url":"https://example.com/jobs/3"}]`, "", 3, false},
		{"small-root", `[{"title":"A","url":"https://example.com/jobs/1"}]`, "", 1, false},
		{"literal-empty-root", `[]`, "", 0, false},
		{"wrapped", `{"data":{"jobs":[{"title":"A","url":"https://example.com/jobs/1"},{"title":"B","url":"https://example.com/jobs/2"},{"title":"C","url":"https://example.com/jobs/3"}]}}`, "", 3, false},
		{"unproved-empty-wrapper", `{"jobs":[]}`, "", 0, true},
		{"changed-small-wrapper", `{"jobs":[{"title":"A","url":"https://example.com/jobs/1"}]}`, "", 0, true},
		{"exact-empty-wrapper", `{"jobs":[],"count":0}`, `,"empty_response":{"jobs":[],"count":0}`, 0, false},
		{"incorrect-empty-wrapper", `{"jobs":[],"count":1}`, `,"empty_response":{"jobs":[],"count":0}`, 0, true},
		{"no-response", "", "", 0, true},
		{"null-path", `[{"title":"A","url":"https://example.com/jobs/1"}]`, `,"json_path":null`, 1, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			options, err := OptionsFromMetadata("https://example.com/careers", `{"api_url":"https://example.com/api","url_field":"url","fields":{"title":"title"}`+c.extra+`}`)
			if err != nil || !options.AutoPath {
				t.Fatal("configuration screening failed", err)
			}
			inventory, err := Discover(context.Background(), options, func(context.Context, Request) (*Document, error) {
				if c.payload == "" {
					return nil, nil
				}
				return Decode([]byte(c.payload))
			}, func(_, source string) (string, error) { return source, nil })
			if (err != nil) != c.failed || len(inventory.Jobs) != c.count || inventory.Truncated {
				t.Fatal("inventory or absence evidence differs", inventory, err)
			}
		})
	}
}

func TestAutomaticAPIPathRequiresExplicitFieldAndURLContracts(t *testing.T) {
	for _, raw := range []string{`{"api_url":"https://example.com/api"}`, `{"api_url":"https://example.com/api","url_field":"url"}`, `{"api_url":"https://example.com/api","fields":{"title":"title"}}`} {
		if _, err := OptionsFromMetadata("https://example.com/careers", raw); err == nil {
			t.Fatal("automatic field or URL inference was admitted")
		}
	}
}
