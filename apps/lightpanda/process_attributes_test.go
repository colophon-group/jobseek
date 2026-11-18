package main

import "testing"

func TestLightpandaProcessAttributesCreateProcessGroup(t *testing.T) {
	attributes, err := lightpandaProcessAttributes(false)
	if err != nil {
		t.Fatal(err)
	}
	if attributes == nil || !attributes.Setpgid {
		t.Fatalf("Lightpanda process attributes = %#v, want Setpgid", attributes)
	}
}
