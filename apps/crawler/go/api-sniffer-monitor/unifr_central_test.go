package apisniffer

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
	"time"
)

func TestUnifrCentralListingsAndDetailsMatchActualPython(t *testing.T) {
	var cases []struct {
		Kind, Name, Locale, Body, Today, Identifier string
		ListingTitle                                string `json:"listing_title"`
		Output                                      map[string]string
		Error                                       bool
	}
	raw, err := os.ReadFile("testdata/python_unifr_central.json")
	if err != nil || json.Unmarshal(raw, &cases) != nil || len(cases) != 22 {
		t.Fatal("actual Python reference missing", err)
	}
	for _, c := range cases {
		t.Run(c.Kind+"/"+c.Name, func(t *testing.T) {
			var fields map[string]string
			var err error
			if c.Kind == "listing" {
				fields, err = UnifrCentralListing([]byte(c.Body), c.Locale)
			} else {
				today, failure := time.Parse("2006-01-02", c.Today)
				if failure != nil {
					t.Fatal(failure)
				}
				fields, err = UnifrCentralDetail([]byte(c.Body), c.Identifier, c.Locale, c.ListingTitle, today)
			}
			if (err != nil) != c.Error || !c.Error && !reflect.DeepEqual(fields, c.Output) {
				t.Fatal(fields, c.Output, err, c.Error)
			}
		})
	}
}
