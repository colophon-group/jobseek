package recruitee

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestFrozenPythonParser(t *testing.T) {
	body, err := os.ReadFile("testdata/python_parser.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows []struct {
		Name     string          `json:"name"`
		Body     json.RawMessage `json:"body"`
		Expected json.RawMessage `json:"expected"`
		Error    bool            `json:"error"`
	}
	if err = json.Unmarshal(body, &rows); err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		t.Run(row.Name, func(t *testing.T) {
			got, err := Parse(row.Body)
			if row.Error {
				if err == nil {
					t.Fatal("accepted Python-invalid fields")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			actual, _ := json.Marshal(got)
			var a, b any
			json.Unmarshal(actual, &a)
			json.Unmarshal(row.Expected, &b)
			if !reflect.DeepEqual(a, b) {
				t.Fatalf("got %s; want %s", actual, row.Expected)
			}
		})
	}
}
