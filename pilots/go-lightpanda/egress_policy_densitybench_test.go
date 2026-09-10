//go:build densitybench

package main

import (
	"net/netip"
	"strings"
	"testing"
)

func TestDensityFixtureCommandUsesOneExactPrivateExemption(t *testing.T) {
	args, err := densityFixtureCommandArgs(9222, netip.MustParseAddr("172.18.0.7"))
	if err != nil {
		t.Fatal(err)
	}
	blockFlags := 0
	for index, arg := range args {
		if arg == "--block-cidrs" {
			blockFlags++
			if index+1 >= len(args) || !strings.HasSuffix(args[index+1], ",-172.18.0.7/32") {
				t.Fatalf("density fixture has no exact /32 exemption: %q", args)
			}
		}
	}
	if blockFlags != 1 {
		t.Fatalf("density --block-cidrs count = %d, want 1", blockFlags)
	}
	if strings.Contains(defaultEgressPolicy().blockCIDRs, "-") {
		t.Fatal("default policy inherited a density fixture exemption")
	}
}

func TestDensityFixtureCommandRejectsNonFixtureAddresses(t *testing.T) {
	for _, address := range []string{"127.0.0.1", "127.0.0.2", "8.8.8.8", "::1", "fd00::2", "0.0.0.0"} {
		t.Run(address, func(t *testing.T) {
			if _, err := densityFixtureCommandArgs(9222, netip.MustParseAddr(address)); err == nil {
				t.Fatalf("density fixture accepted %s", address)
			}
		})
	}
}

func TestDensityFixtureHostnameIsClosedToEmbeddedOrigins(t *testing.T) {
	for _, host := range []string{"origin-0.bench.test", "origin-7.bench.test"} {
		if !densityFixtureHostname(host) {
			t.Fatalf("rejected embedded density host %q", host)
		}
	}
	for _, host := range []string{"origin-8.bench.test", "origin-0.bench.test.example", "127.0.0.2", ""} {
		if densityFixtureHostname(host) {
			t.Fatalf("accepted non-workload density host %q", host)
		}
	}
}
