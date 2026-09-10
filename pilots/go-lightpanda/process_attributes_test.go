package main

import "testing"

func TestLightpandaProcessAttributesCreateProcessGroup(t *testing.T) {
	attributes := lightpandaProcessAttributes()
	if attributes == nil || !attributes.Setpgid {
		t.Fatalf("Lightpanda process attributes = %#v, want Setpgid", attributes)
	}
}
