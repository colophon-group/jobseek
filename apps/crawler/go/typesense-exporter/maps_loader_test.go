package main

import (
	"reflect"
	"testing"
)

func TestTaxonomyAncestorChains(t *testing.T) {
	parents := map[int]*int{10: ptr(20), 20: ptr(30), 30: nil}
	chain, err := parentChain(10, parents)
	if err != nil || !reflect.DeepEqual(chain, []int{10, 20, 30}) {
		t.Fatalf("chain %v: %v", chain, err)
	}
	locations, err := locationAncestors(10, parents, map[int][]int{30: {80, 90}})
	if err != nil || !reflect.DeepEqual(locations, []int{10, 20, 30, 80, 90}) {
		t.Fatalf("locations %v: %v", locations, err)
	}
	parents[30] = ptr(10)
	if _, err := parentChain(10, parents); err == nil {
		t.Fatal("accepted taxonomy parent cycle")
	}
}
