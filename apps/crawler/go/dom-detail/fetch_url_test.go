package dom

import (
	"encoding/json"
	"os"
	"testing"
)

func TestAlternateFetchURLOriginalPythonContract(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_alternate_fetch_url.json")
	var rows []struct {
		Name, Source string
		Config       any
		Output       *string
		Error        bool
	}
	if e != nil || json.Unmarshal(raw, &rows) != nil || len(rows) != 23 {
		t.Fatal("original references unavailable")
	}
	for _, r := range rows {
		t.Run(r.Name, func(t *testing.T) {
			got, e := AlternateFetchURL(r.Source, r.Config)
			if (e != nil) != r.Error {
				t.Fatal("originalfailure differs", e)
			}
			if !r.Error && (r.Output == nil || got != *r.Output) {
				t.Fatal("original rewrittenURL differs")
			}
		})
	}
}
