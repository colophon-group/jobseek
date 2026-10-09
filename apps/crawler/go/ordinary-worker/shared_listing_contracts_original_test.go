package worker

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSharedListingContractsActualOriginalCurrentOutputs(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_shared_listing_contracts.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []sharedServiceOriginalCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 13 {
		t.Fatal("original thirteen-case oracle unavailable")
	}
	runServiceAnnotationOriginalCases(t, cases)
}
