package apisniffer

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"testing"
)

func TestAutomaticFieldsMatchOriginalPythonUniqueMappings(t *testing.T) {
	var cases []struct {
		Name     string
		Items    json.RawMessage
		Expected map[string]string
	}
	raw, e := os.ReadFile("testdata/python_auto_fields.json")
	if e != nil || json.Unmarshal(raw, &cases) != nil {
		t.Fatal("original oracle unavailable", e)
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			d, e := Decode(c.Items)
			if e != nil {
				t.Fatal(e)
			}
			items, e := d.items("", false)
			if e != nil {
				t.Fatal(e)
			}
			rows := []inventorySourceRow{}
			for _, item := range items {
				rows = append(rows, inventorySourceRow{d, item})
			}
			mapping, e := autoMapFields(rows)
			if e != nil {
				t.Fatal(e)
			}
			encoded, _ := json.Marshal(mapping)
			var normalized map[string]string
			json.Unmarshal(encoded, &normalized)
			if !reflect.DeepEqual(normalized, c.Expected) {
				t.Fatal("actual Python mapping differs", normalized, c.Expected)
			}
		})
	}
}
func TestAmbiguousAutomaticFieldsRequireExplicitMappings(t *testing.T) {
	for _, input := range []string{`[{"title":"Engineer","name":"Different"}]`, `[{"body":"One","description":"Two"}]`, `[{"location":"Paris","office":"London"}]`, `[{"team":"One","department":"Two"}]`} {
		d, e := Decode([]byte(input))
		if e != nil {
			t.Fatal(e)
		}
		rows, e := d.items("", false)
		if e != nil {
			t.Fatal(e)
		}
		_, e = autoMapFields([]inventorySourceRow{{d, rows[0]}})
		if !errors.Is(e, ErrAmbiguousAutoFields) {
			t.Fatal("arbitrary automatic mapping accepted", e)
		}
	}
}
