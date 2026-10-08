package dom

import (
	"encoding/json"
	"os"
	"testing"
)

func TestDOMListingProofsMatchActualPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_inventory_proofs.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		DOMCases []struct {
			Name, Body string
			Config     Object
			Count      int
			Error      bool
		} `json:"dom_cases"`
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.DOMCases) != 25 {
		t.Fatal("actual Python proof corpus unavailable")
	}
	for _, c := range fixture.DOMCases {
		t.Run(c.Name, func(t *testing.T) {
			config, err := ListingOptions(c.Config, "https://example.com/careers")
			if err == nil {
				err = ValidateListingProofs(c.Body, config, c.Count)
			}
			if (err != nil) != c.Error {
				t.Fatal("Python completeness proof changed", err, c.Error)
			}
		})
	}
}
