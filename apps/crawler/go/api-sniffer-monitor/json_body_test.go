package apisniffer

import (
	"encoding/json"
	"os"
	"testing"
)

func TestConfiguredAndPaginatedBodiesMatchPythonJSONBytes(t *testing.T) {
	b, err := os.ReadFile("testdata/python_json_bodies.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct{ Raw, Compact, Spaced string }
	if err = json.Unmarshal(b, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		d, err := Decode([]byte(c.Raw))
		if err != nil {
			t.Fatal(err)
		}
		for _, spaced := range []bool{false, true} {
			got, err := d.jsonBody(d.Value, spaced)
			want := c.Compact
			if spaced {
				want = c.Spaced
			}
			if err != nil || got != want {
				t.Fatalf("configured POST bytes differ: %q / %q (%v)", got, want, err)
			}
		}
	}
}
