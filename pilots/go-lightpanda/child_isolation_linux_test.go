//go:build linux && !densitybench

package main

import (
	"os"
	"syscall"
	"testing"
)

func TestRuntimeCPUQuotaDescriptorRejectsOrdinaryFileAndOtherTargets(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "private-key")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	readOnly, err := os.Open(f.Name())
	if err != nil {
		t.Fatal(err)
	}
	defer readOnly.Close()
	if runtimeCPUQuotaDescriptor(int(f.Fd()), "/sys/fs/cgroup/cpu.max") ||
		runtimeCPUQuotaDescriptor(int(readOnly.Fd()), "/sys/fs/cgroup/cpu.max") ||
		runtimeCPUQuotaDescriptor(int(f.Fd()), f.Name()) ||
		runtimeCPUQuotaDescriptor(-1, "/sys/fs/cgroup/cpu.max") {
		t.Fatal("ordinary or unavailable descriptor passed quota attestation")
	}
}

func TestRuntimeCPUQuotaDescriptorRequiresCloseOnExecOnActualCgroup(t *testing.T) {
	f, err := os.Open("/sys/fs/cgroup/cpu.max")
	if err != nil {
		t.Skip("runner has no root cgroup-v2 CPU quota file")
	}
	defer f.Close()
	if !runtimeCPUQuotaDescriptor(int(f.Fd()), "/sys/fs/cgroup/cpu.max") {
		t.Fatal("read-only close-on-exec cgroup quota descriptor refused")
	}
	if runtimeCPUQuotaDescriptor(int(f.Fd()), "/sys/fs/cgroup/memory.max") {
		t.Fatal("unrelated cgroup descriptor accepted")
	}
	duplicate, err := syscall.Dup(int(f.Fd()))
	if err != nil {
		t.Fatal(err)
	}
	defer syscall.Close(duplicate)
	if runtimeCPUQuotaDescriptor(duplicate, "/sys/fs/cgroup/cpu.max") {
		t.Fatal("inheritable cgroup quota descriptor accepted")
	}
}
