package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestNextdataRichProjectionMatchesActualPythonTemplatesFieldsAndSlugs(t *testing.T) {
	data, err := os.ReadFile("testdata/python_nextdata_inventory.json")
	var corpus struct {
		Cases []struct {
			Name, Template string
			Items          json.RawMessage
			SlugFields     []string `json:"slug_fields"`
			Fields         map[string]any
			Expected       []map[string]any
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 7 {
		t.Fatal("actual NextData inventory corpus unavailable")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			doc, err := Decode(c.Items)
			if err != nil {
				t.Fatal(err)
			}
			jobs, err := doc.ProjectNextdataItems(doc.Value.([]any), c.Template, c.SlugFields, c.Fields)
			if err != nil {
				t.Fatal(err)
			}
			body, _ := json.Marshal(jobs)
			var actual []map[string]any
			_ = json.Unmarshal(body, &actual)
			for _, j := range c.Expected {
				delete(j, "language")
				delete(j, "base_salary")
				delete(j, "localizations")
				delete(j, "source_identity")
			}
			if !reflect.DeepEqual(actual, c.Expected) {
				t.Fatalf("NextData rich projection differs: %#v / %#v", actual, c.Expected)
			}
		})
	}
}
