package worker

import (
	"encoding/json"
	"os"
	"testing"
)

func TestSharedInventoryOptionsActualOriginalCurrentOutputs(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_shared_inventory_options.json")
	if e != nil {
		t.Fatal(e)
	}
	var cases []sharedServiceOriginalCase
	if json.Unmarshal(raw, &cases) != nil || len(cases) != 14 {
		t.Fatal("original fourteen-board oracle unavailable")
	}
	runServiceAnnotationOriginalCases(t, cases)
}
