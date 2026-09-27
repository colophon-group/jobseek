package main

import (
	"encoding/json"
	"math"
	"os/exec"
	"strings"
	"testing"
)

func TestTaxonomyCanonicalJSONMatchesPythonEvidence(t *testing.T) {
	python, err := exec.LookPath("python3")
	if err != nil {
		t.Skip("python3 is unavailable for offline oracle")
	}
	fixtures := []string{
		`{"coordinates":[1.0,-0.0],"aliases":["Zürich","<>&","A\u2028B","\u007f","😀"],"id":"1"}`,
		`{"values":[0.0001,0.00001,1000000.0,1000000000000000.0,1e16,1e20,1e-30,1.2345678901234567]}`,
		`{"values":[9223372036854775807,-9223372036854775808,1.0,null,true,false,"\b\f\n\r\t\u0000"]}`,
	}
	for _, ascii := range []bool{false, true} {
		for _, fixture := range fixtures {
			decoder := json.NewDecoder(strings.NewReader(fixture))
			decoder.UseNumber()
			var value any
			if err := decoder.Decode(&value); err != nil {
				t.Fatal(err)
			}
			actual, err := taxonomyCanonicalJSON(value, ascii)
			if err != nil {
				t.Fatal(err)
			}
			flag := "False"
			if ascii {
				flag = "True"
			}
			command := exec.Command(python, "-c", "import json,sys;sys.stdout.write(json.dumps(json.load(sys.stdin),ensure_ascii="+flag+",separators=(',',':'),sort_keys=True))")
			command.Stdin = strings.NewReader(fixture)
			expected, err := command.Output()
			if err != nil {
				t.Fatal(err)
			}
			if string(actual) != string(expected) {
				t.Fatalf("Python evidence differs:\nGo: %s\nPython: %s", actual, expected)
			}
		}
	}
	actual, err := taxonomyCanonicalJSON(map[string]any{"coordinates": []float64{1, math.Copysign(0, -1)}, "id": "1"}, false)
	if err != nil || string(actual) != `{"coordinates":[1.0,-0.0],"id":"1"}` {
		t.Fatalf("native projection changed: %s %v", actual, err)
	}
}
