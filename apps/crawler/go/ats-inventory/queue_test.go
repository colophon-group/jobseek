package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestQueueClassificationAndCapsMatchFrozenPython(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_queue.json")
	if e != nil {
		t.Fatal(e)
	}
	var oracle struct {
		Items    []githubWorkItem
		Claims   []githubClaim
		Now      string
		Snapshot inventoryQueueSnapshot
		Policies []struct {
			Policy       inventoryQueuePolicy
			CreatedToday int `json:"created_today"`
			Requested    int
			Status       string
		}
	}
	if e = json.Unmarshal(raw, &oracle); e != nil {
		t.Fatal(e)
	}
	now, e := parseInventoryTime(oracle.Now)
	if e != nil {
		t.Fatal(e)
	}
	got := classifyInventoryQueue(oracle.Items, oracle.Claims, now, 4*3600)
	if !reflect.DeepEqual(got, oracle.Snapshot) {
		t.Fatalf("Python claim/link/latency classification differs: got %#v want %#v", got, oracle.Snapshot)
	}
	for i, c := range oracle.Policies {
		if e = c.Policy.validate(); e != nil {
			t.Fatal(e)
		}
		n, status := requestedInventoryCreates(got, c.CreatedToday, c.Policy)
		if n != c.Requested || status != c.Status {
			t.Fatalf("Python queue cap case %d differs", i)
		}
	}
}
