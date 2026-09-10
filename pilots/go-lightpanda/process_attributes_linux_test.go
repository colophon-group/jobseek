//go:build linux

package main

import (
	"syscall"
	"testing"
)

func TestLightpandaProcessAttributesKillChildWhenParentDies(t *testing.T) {
	attributes := lightpandaProcessAttributes()
	if attributes == nil || attributes.Pdeathsig != syscall.SIGKILL {
		t.Fatalf("Lightpanda parent-death signal = %#v, want SIGKILL", attributes)
	}
}
