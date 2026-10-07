package queue

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestRSSPaginationMatchesActualPython(t *testing.T) {
	var corpus struct {
		Cases []struct {
			Name, Preset string
			Metadata     json.RawMessage
			Expected     *RSSPagination
			Error        bool
		}
	}
	raw, err := os.ReadFile("testdata/python_rss_pagination.json")
	if err != nil || json.Unmarshal(raw, &corpus) != nil || len(corpus.Cases) != 16 {
		t.Fatal("actual Python pagination corpus missing", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			got, err := RSSPaginationOptions(string(c.Metadata), c.Preset)
			if (err != nil) != c.Error || !c.Error && !reflect.DeepEqual(got, c.Expected) {
				t.Fatal("legacy pagination options differ", got, c.Expected, err, c.Error)
			}
		})
	}
}
