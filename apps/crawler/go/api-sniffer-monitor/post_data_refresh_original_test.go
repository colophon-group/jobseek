package apisniffer

import (
	"encoding/json"
	"os"
	"testing"
)

func TestPOSTRefreshFrozenOriginalBodyProjection(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_post_data_refresh.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []struct {
		Name, Body, Source string
		ExpectedBody       string `json:"expected_body"`
		Refresh            map[string]any
		Error              bool
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 12 {
		t.Fatal("original refresh corpus invalid")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			md, _ := json.Marshal(map[string]any{"api_url": "https://example.com/api", "method": "POST", "json_path": "jobs", "post_data": c.Body, "post_data_refresh": c.Refresh})
			o, e := OptionsFromMetadata("https://example.com/careers", string(md))
			if e != nil {
				if !c.Error {
					t.Fatal("supported original configuration rejected", e)
				}
				return
			}
			updated, e := o.refreshedPostData(c.Source)
			if c.Error {
				if e == nil {
					t.Fatal("original refresh failure became success")
				}
				return
			}
			if e != nil || updated.Body != c.ExpectedBody || o.Body != c.Body {
				t.Fatal("original body bytes or immutable input changed", e)
			}
		})
	}
}
