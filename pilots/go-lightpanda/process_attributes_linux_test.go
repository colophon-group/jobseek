//go:build linux

package main

import (
	"slices"
	"syscall"
	"testing"
)

func TestLightpandaProcessAttributesKillChildWhenParentDies(t *testing.T) {
	attributes, err := lightpandaProcessAttributes(false)
	if err != nil {
		t.Fatal(err)
	}
	if attributes == nil || attributes.Pdeathsig != syscall.SIGKILL {
		t.Fatalf("Lightpanda parent-death signal = %#v, want SIGKILL", attributes)
	}
}

func TestIsolatedLightpandaProcessAttributesUseDistinctIdentity(t *testing.T) {
	attributes, err := lightpandaProcessAttributes(true)
	if err != nil {
		t.Fatal(err)
	}
	credential := attributes.Credential
	if credential == nil || credential.Uid != lightpandaChildUID || credential.Gid != lightpandaChildGID {
		t.Fatalf("Lightpanda child credential = %#v", credential)
	}
	if credential.NoSetGroups || len(credential.Groups) != 1 || credential.Groups[0] != lightpandaChildGID {
		t.Fatalf("Lightpanda supplementary-group clearing = %#v", credential)
	}
}

func TestIsolatedLightpandaCommandUsesCapabilityClearingTrampoline(t *testing.T) {
	command, err := (commandStarter{
		binary:       lightpandaServiceBinary,
		egressPolicy: defaultEgressPolicy(),
		isolateChild: true,
	}).buildCommand(9222, &boundedBuffer{limit: maxProcessLogBytes})
	if err != nil {
		t.Fatal(err)
	}
	expectedPrefix := []string{
		lightpandaPrivilegeTrampoline,
		"--inh-caps=-all",
		"--ambient-caps=-all",
		"--nnp",
		"--",
		lightpandaServiceBinary,
	}
	if len(command.Args) <= len(expectedPrefix) || !slices.Equal(command.Args[:len(expectedPrefix)], expectedPrefix) {
		t.Fatalf("isolated command = %#v", command.Args)
	}
	if !slices.Equal(command.Env, lightpandaChildEnvironment) {
		t.Fatalf("isolated child environment = %#v", command.Env)
	}
	credential := command.SysProcAttr.Credential
	if credential == nil || credential.Uid != lightpandaChildUID || credential.Gid != lightpandaChildGID {
		t.Fatalf("isolated child credential = %#v", credential)
	}
}
