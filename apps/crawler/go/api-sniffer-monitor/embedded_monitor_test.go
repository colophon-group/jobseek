package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestEmbeddedMonitorDocumentsMatchActualPythonSourcesAndNumberIdentity(t *testing.T) {
	data, err := os.ReadFile("testdata/python_embedded_monitor.json")
	var corpus struct {
		Cases []struct {
			Name, HTML, Source string
			Expected           json.RawMessage
		}
	}
	if err != nil || json.Unmarshal(data, &corpus) != nil || len(corpus.Cases) != 14 {
		t.Fatal("actual embedded monitor corpus unavailable")
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := ParseEmbeddedMonitorDocument(c.HTML, c.Source)
			if string(c.Expected) == "null" {
				if err == nil {
					t.Fatal("malformed or missing document accepted")
				}
				return
			}
			want, failure := Decode(c.Expected)
			if err != nil || failure != nil || !reflect.DeepEqual(got.Value, want.Value) {
				t.Fatal("embedded source semantics differ", err)
			}
		})
	}
}
