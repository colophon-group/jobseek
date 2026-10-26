package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestTupu360ListingOriginalPythonParserContract(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_tupu360_listing.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name, HTML, Board string
		Rejected          bool
		Total             int
		Jobs              []map[string]any
	}
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 16 {
		t.Fatal("original Python oracle unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			total, jobs, err := ParseTupu360Listing(c.HTML, c.Board)
			if c.Rejected {
				if err == nil || len(jobs) != 0 {
					t.Fatal("original rejection changed")
				}
				return
			}
			if err != nil || total != c.Total {
				t.Fatal("original total changed", err)
			}
			b, _ := json.Marshal(jobs)
			var normalized []map[string]any
			json.Unmarshal(b, &normalized)
			if !reflect.DeepEqual(normalized, c.Jobs) {
				t.Fatal("original rich card fields changed")
			}
		})
	}
	for _, board := range []string{"http://fixture.tupu360.com/fixture/position/index", "https://fixture.tupu360.com/fixture/position/index?filter=1", "https://fixture.tupu360.com:443/fixture/position/index", "https://tupu360.com/fixture/position/index", "https://fixture.tupu360.com/fixture/position/detail"} {
		if Tupu360ListingURL(board) {
			t.Fatal("filtered/non-root listing admitted")
		}
	}
}
