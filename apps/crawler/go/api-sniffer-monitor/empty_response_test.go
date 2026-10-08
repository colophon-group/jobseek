package apisniffer

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestAPIEmptyResponseMatchesActualPython(t *testing.T) {
	raw, err := os.ReadFile("../dom-detail/testdata/python_inventory_proofs.json")
	if err != nil {
		t.Fatal(err)
	}
	d, err := Decode(raw)
	if err != nil {
		t.Fatal(err)
	}
	cases := d.Value.(map[string]any)["api_cases"].([]any)
	if len(cases) != 17 {
		t.Fatal("actual Python proof corpus unavailable")
	}
	for _, v := range cases {
		c := v.(map[string]any)
		t.Run(c["name"].(string), func(t *testing.T) {
			body, _ := json.Marshal(c["payload"])
			document, err := Decode(body)
			if err != nil {
				t.Fatal(err)
			}
			got, err := document.MatchesEmptyResponse(c["config"].(map[string]any))
			if c["error"] == true {
				if err == nil {
					t.Fatal("Python-invalid marker accepted")
				}
				return
			}
			if err != nil || got != c["matches"] {
				t.Fatal("Python scalar/empty marker changed", got, err, c["matches"])
			}
		})
	}
}

func TestAPIConfiguredEmptyInventoryCannotAcceptMissingResponse(t *testing.T) {
	o, err := OptionsFromMetadata("https://example.com/careers", `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url","fields":{"title":"title"},"empty_response":{"jobs":[],"found_jobs":false}}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, body := range []string{"", `{"jobs":[]}`, `{"jobs":[],"found_jobs":0}`, `{"jobs":[],"found_jobs":false}`} {
		fetch := func(context.Context, Request) (*Document, error) {
			if body == "" {
				return nil, nil
			}
			return Decode([]byte(body))
		}
		got, err := Discover(context.Background(), o, fetch, func(base, source string) (string, error) { return source, nil })
		if (err == nil) != (body == `{"jobs":[],"found_jobs":false}`) || len(got.Jobs) != 0 {
			t.Fatal("missing/unproved empty response accepted", body, err)
		}
	}
}
