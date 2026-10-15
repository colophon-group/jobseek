package dom

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
)

func TestLinkedDescriptionOriginalURLContract(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_linked_description_urls.json")
	var rows []struct {
		Source, HTML string
		Config       any
		Output       *string
		Error        bool
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	if e != nil || d.Decode(&rows) != nil || len(rows) != 6 {
		t.Fatal("originalreferencesmissing")
	}
	for i, r := range rows {
		o, e := LinkedDescriptionConfig(r.Config)
		if e != nil {
			t.Fatal(i, e)
		}
		got, e := o.Target(r.HTML, r.Source)
		if (e != nil) != r.Error {
			t.Fatal(i, "originalfailure differs")
		}
		if !r.Error && (r.Output == nil || got != *r.Output) {
			t.Fatal(i, "originaltarget differs")
		}
	}
}
