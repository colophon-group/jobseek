package successfactorsrss

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestFrozenPythonParserParity(t *testing.T) {
	body, err := os.ReadFile("testdata/python_parser.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Feed  string
		Items int
		Jobs  []map[string]any
	}
	if err := json.Unmarshal(body, &rows); err != nil {
		t.Fatal(err)
	}
	for index, row := range rows {
		jobs, items, err := ParsePage([]byte(row.Feed))
		if err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		encoded, err := json.Marshal(jobs)
		if err != nil {
			t.Fatal(err)
		}
		var actual []map[string]any
		if err := json.Unmarshal(encoded, &actual); err != nil {
			t.Fatal(err)
		}
		if items != row.Items || !reflect.DeepEqual(actual, row.Jobs) {
			t.Fatalf("case %d: got %s items=%d; want %+v items=%d", index, encoded, items, row.Jobs, row.Items)
		}
	}
}
