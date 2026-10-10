package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestOriginalPythonJMESPathLiteralParity(t *testing.T) {
	body, e := os.ReadFile("testdata/python_jmespath_literals.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixtures []struct {
		Name, Query  string
		Data, Result any
		Failed       bool
	}
	if json.Unmarshal(body, &fixtures) != nil || len(fixtures) != 20 {
		t.Fatal("invalid frozen original fixture")
	}
	for _, f := range fixtures {
		t.Run(f.Name, func(t *testing.T) {
			got, e := searchJMESPath(f.Query, f.Data)
			if (e != nil) != f.Failed {
				t.Fatal("original expression outcome differs")
			}
			if !f.Failed && !reflect.DeepEqual(got, f.Result) {
				t.Fatalf("original expression value differs: got %#v want %#v", got, f.Result)
			}
		})
	}
}
