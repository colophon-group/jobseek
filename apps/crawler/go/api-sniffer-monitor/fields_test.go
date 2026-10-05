package apisniffer

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestExistingPythonFieldOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_fields.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name                       string `json:"name"`
		Root, Item, Spec, Expected json.RawMessage
		Error                      bool
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, err := Decode(c.Root)
			if err != nil {
				t.Fatal(err)
			}
			item, err := d.decode(jsonDecoder(c.Item))
			if err != nil {
				t.Fatal(err)
			}
			var spec, expected any
			if err := json.Unmarshal(c.Spec, &spec); err != nil {
				t.Fatal(err)
			}
			if len(c.Expected) > 0 {
				if err := json.Unmarshal(c.Expected, &expected); err != nil {
					t.Fatal(err)
				}
			}
			actual, err := d.Field(item, spec)
			if c.Error {
				if err == nil {
					t.Fatalf("expected rejection; got %#v", actual)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(actual, expected) {
				t.Fatalf("got %#v; Python %#v", actual, expected)
			}
		})
	}
}

func jsonDecoder(body []byte) *json.Decoder {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	return d
}
