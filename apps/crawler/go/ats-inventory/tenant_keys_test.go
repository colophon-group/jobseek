package main

import (
	"encoding/json"
	"os"
	"testing"
)

func TestTenantKeysMatchFrozenPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_tenant_keys.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		Rows []struct {
			Family string
			URL    any
			ATSID  any `json:"ats_id"`
			Key    *string
		}
		Identities []struct {
			Value      any
			Normalized *string
		}
	}
	if e = json.Unmarshal(raw, &oracle); e != nil {
		t.Fatal(e)
	}
	for i, row := range oracle.Rows {
		want := ""
		if row.Key != nil {
			want = *row.Key
		}
		if got := inventoryTenantKey(row.Family, row.URL, row.ATSID); got != want {
			t.Fatalf("Python tenant case %d (%s): got %q want %q", i, row.Family, got, want)
		}
	}
	for i, row := range oracle.Identities {
		want := ""
		if row.Normalized != nil {
			want = *row.Normalized
		}
		if got := normalizeInventoryIdentity(row.Value, 500); got != want {
			t.Fatalf("Python identity case %d: got %q want %q", i, got, want)
		}
	}
}
