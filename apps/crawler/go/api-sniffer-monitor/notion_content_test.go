package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestNotionDetailRenderingAndPropertyPrecedenceMatchActualPython(t *testing.T) {
	var corpus []struct {
		Name   string
		PageID string `json:"page_id"`
		Data   json.RawMessage
		Config struct {
			PropertyMap map[string]string `json:"property_map"`
		}
		Output map[string]any
		Error  bool
	}
	raw, err := os.ReadFile("testdata/python_notion_detail.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus) != 10 {
		t.Fatal("actual Python Notion corpus missing", err)
	}
	for _, c := range corpus {
		t.Run(c.Name, func(t *testing.T) {
			// The fixture retains the actual response bytes and insertion order.
			d, err := Decode(c.Data)
			if err != nil {
				t.Fatal(err)
			}
			fields, err := NotionDetailFields(d, c.PageID, c.Config.PropertyMap)
			if (err != nil) != c.Error {
				t.Fatal(fields, err, c.Error)
			}
			if c.Error {
				return
			}
			for _, key := range []string{"title", "description", "locations", "employment_type", "job_location_type", "metadata"} {
				value := fields[key]
				if value == "" {
					value = nil
				}
				if md, ok := value.(map[string]any); ok && len(md) == 0 {
					value = nil
				}
				// Decode the Go result as the same JSON value types as the frozen output.
				body, _ := json.Marshal(value)
				var actual any
				if json.Unmarshal(body, &actual) != nil {
					t.Fatal(key)
				}
				if !reflect.DeepEqual(actual, c.Output[key]) {
					t.Fatal(key, actual, c.Output[key])
				}
			}
		})
	}
}

func TestNotionMissingReferencedBlockRejectsPartialDescription(t *testing.T) {
	d, err := Decode([]byte(`{"recordMap":{"block":{"page":{"value":{"properties":{"title":[["Engineer"]]},"content":["present","missing"]}},"present":{"value":{"type":"text","properties":{"title":[["First paragraph"]]}}}}}}`))
	if err != nil {
		t.Fatal(err)
	}
	if fields, err := NotionDetailFields(d, "page", nil); err == nil || fields != nil {
		t.Fatal("partial description accepted", fields, err)
	}
}
