package main

import (
	"context"
	"reflect"
	"strings"
	"testing"
)

func TestDefaultEgressPolicyIsCanonicalAndComplete(t *testing.T) {
	policy := defaultEgressPolicy()
	if err := policy.validate(); err != nil {
		t.Fatal(err)
	}
	canonical, err := canonicalBlockedCIDRs(strings.Split(baselineBlockedCIDRs, ","))
	if err != nil {
		t.Fatal(err)
	}
	if policy.blockCIDRs != canonical {
		t.Fatalf("default CIDRs = %q, want %q", policy.blockCIDRs, canonical)
	}
	for _, required := range []string{
		"0.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
		"224.0.0.0/4", "240.0.0.0/4", "::/3", "2001::/23", "2002::/16",
		"4000::/2", "8000::/1",
	} {
		if !strings.Contains(","+canonical+",", ","+required+",") {
			t.Errorf("default CIDRs omitted %q", required)
		}
	}
}

func TestNewEgressPolicyCanonicalizesTrustedAdditionalCIDRs(t *testing.T) {
	policy, err := newEgressPolicy([]string{"93.184.216.34/32", "8.8.8.0/24"})
	if err != nil {
		t.Fatal(err)
	}
	if err := policy.validate(); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(policy.blockCIDRs, "0.0.0.0/8,8.8.8.0/24,10.0.0.0/8") ||
		!strings.Contains(policy.blockCIDRs, ",93.184.216.34/32,") {
		t.Fatalf("additional CIDRs were not deterministically sorted: %q", policy.blockCIDRs)
	}
}

func TestNewEgressPolicyRejectsUnsafeOrAmbiguousCIDRs(t *testing.T) {
	tests := []struct {
		name       string
		additional []string
	}{
		{name: "empty", additional: []string{""}},
		{name: "leading whitespace", additional: []string{" 93.184.216.34/32"}},
		{name: "trailing whitespace", additional: []string{"93.184.216.34/32\t"}},
		{name: "allow exemption", additional: []string{"-127.0.0.1/32"}},
		{name: "missing prefix", additional: []string{"93.184.216.34"}},
		{name: "host bits", additional: []string{"93.184.216.34/24"}},
		{name: "noncanonical IPv6", additional: []string{"2001:4860:0000::/48"}},
		{name: "IPv4-mapped IPv6", additional: []string{"::ffff:93.184.216.34/128"}},
		{name: "duplicate baseline", additional: []string{"127.0.0.0/8"}},
		{name: "overlap baseline", additional: []string{"127.0.0.1/32"}},
		{name: "duplicate additional", additional: []string{"8.8.8.8/32", "8.8.8.8/32"}},
		{name: "overlapping additional", additional: []string{"8.8.8.0/24", "8.8.8.8/32"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := newEgressPolicy(test.additional); err == nil {
				t.Fatalf("newEgressPolicy(%q) succeeded", test.additional)
			}
		})
	}
	tooMany := make([]string, maxEgressCIDRCount)
	for index := range tooMany {
		tooMany[index] = "8.8.8.8/32"
	}
	if _, err := newEgressPolicy(tooMany); err == nil {
		t.Fatal("newEgressPolicy accepted more than the bounded CIDR count")
	}
}

func TestRuntimeV1ServiceEgressPolicyReconcilesDeploymentInventory(t *testing.T) {
	policy, err := newRuntimeV1ServiceEgressPolicy([]string{
		"10.0.0.5/32",      // Already covered by the registry baseline.
		"93.184.216.34/32", // Deployment-global address must be added.
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := policy.validate(); err != nil {
		t.Fatal(err)
	}
	if strings.Count(policy.egressPolicy.blockCIDRs, "10.0.0.0/8") != 1 ||
		strings.Contains(policy.egressPolicy.blockCIDRs, "10.0.0.5/32") {
		t.Fatalf("baseline-covered inventory was duplicated: %q", policy.egressPolicy.blockCIDRs)
	}
	if !strings.Contains(","+policy.egressPolicy.blockCIDRs+",", ",93.184.216.34/32,") {
		t.Fatalf("globally routed deployment address is not blocked: %q", policy.egressPolicy.blockCIDRs)
	}
	if policy.egressPolicy.blockCIDRs == defaultEgressPolicy().blockCIDRs {
		t.Fatal("service-qualified policy collapsed to the baseline-only default")
	}
}

func TestRuntimeV1ServiceEgressPolicyAllowsBaselineCoveredHostAndProjectOverlap(t *testing.T) {
	policy, err := newRuntimeV1ServiceEgressPolicy([]string{
		"10.0.0.0/16",
		"10.0.0.5/32",
		"93.184.216.34/32",
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := policy.validate(); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(policy.egressPolicy.blockCIDRs, "10.0.0.0/16") ||
		strings.Contains(policy.egressPolicy.blockCIDRs, "10.0.0.5/32") {
		t.Fatalf("baseline-covered host/project entries were duplicated: %q", policy.egressPolicy.blockCIDRs)
	}
}

func TestRuntimeV1ServiceQualifiedPolicyReachesExactCommandArgument(t *testing.T) {
	qualified, err := newRuntimeV1ServiceEgressPolicy([]string{
		"10.0.0.0/16", "10.0.0.5/32", "93.184.216.34/32",
	})
	if err != nil {
		t.Fatal(err)
	}
	command, err := (commandStarter{
		binary:       "lightpanda",
		egressPolicy: qualified.egressPolicy,
	}).buildCommand(9222, &boundedBuffer{limit: maxProcessLogBytes})
	if err != nil {
		t.Fatal(err)
	}
	blockFlags := 0
	for index, argument := range command.Args {
		if strings.Contains(strings.ToLower(argument), "proxy") {
			t.Fatalf("service Lightpanda argv contains proxy configuration: %q", command.Args)
		}
		if argument != "--block-cidrs" {
			continue
		}
		blockFlags++
		if index+1 >= len(command.Args) || command.Args[index+1] != qualified.egressPolicy.blockCIDRs {
			t.Fatalf("service --block-cidrs does not carry the qualified policy: %q", command.Args)
		}
		if !strings.Contains(","+command.Args[index+1]+",", ",93.184.216.34/32,") ||
			strings.HasPrefix(command.Args[index+1], "-") || strings.Contains(command.Args[index+1], ",-") {
			t.Fatalf("service --block-cidrs is incomplete or contains an allow exemption: %q", command.Args[index+1])
		}
	}
	if blockFlags != 1 {
		t.Fatalf("service --block-cidrs count = %d, want 1", blockFlags)
	}
}

func TestRuntimeV1ServiceEgressPolicyRequiresBoundedUncoveredGlobalInventory(t *testing.T) {
	tests := []struct {
		name      string
		inventory []string
	}{
		{name: "missing"},
		{name: "baseline only", inventory: []string{"10.0.0.5/32"}},
		{name: "empty", inventory: []string{""}},
		{name: "leading whitespace", inventory: []string{" 93.184.216.34/32"}},
		{name: "trailing whitespace", inventory: []string{"93.184.216.34/32 "}},
		{name: "allow exemption", inventory: []string{"-93.184.216.34/32"}},
		{name: "host bits", inventory: []string{"93.184.216.34/24"}},
		{name: "mapped IPv4", inventory: []string{"::ffff:93.184.216.34/128"}},
		{name: "duplicate", inventory: []string{"93.184.216.34/32", "93.184.216.34/32"}},
		{name: "uncovered overlap", inventory: []string{"93.184.216.0/24", "93.184.216.34/32"}},
		{name: "partial baseline overlap", inventory: []string{"0.0.0.0/1"}},
		{name: "uncovered non-global", inventory: []string{"ff00::/8"}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if _, err := newRuntimeV1ServiceEgressPolicy(test.inventory); err == nil {
				t.Fatalf("service inventory accepted: %q", test.inventory)
			}
		})
	}

	tooMany := make([]string, maxEgressCIDRCount-len(strings.Split(baselineBlockedCIDRs, ","))+1)
	for index := range tooMany {
		// Duplicate values are intentional: the entry-count gate must run first.
		tooMany[index] = "93.184.216.34/32"
	}
	if _, err := newRuntimeV1ServiceEgressPolicy(tooMany); err == nil {
		t.Fatal("service inventory accepted more than its bounded entry count")
	}

	operationalValue := "93.184.216.34/024"
	if _, err := newRuntimeV1ServiceEgressPolicy([]string{operationalValue}); err == nil {
		t.Fatal("noncanonical operational value was accepted")
	} else if strings.Contains(err.Error(), operationalValue) {
		t.Fatal("validation error echoed an operational CIDR value")
	}
}

func TestRuntimeV1ServiceEgressPolicyRejectsTamperingAndInventoryDrift(t *testing.T) {
	inventory := []string{"10.0.0.5/32", "93.184.216.34/32"}
	policy, err := newRuntimeV1ServiceEgressPolicy(inventory)
	if err != nil {
		t.Fatal(err)
	}
	for _, invalid := range []runtimeV1ServiceEgressPolicy{
		{},
		{egressPolicy: defaultEgressPolicy(), canonicalDeploymentCIDRs: policy.canonicalDeploymentCIDRs},
		{egressPolicy: policy.egressPolicy, canonicalDeploymentCIDRs: "93.184.216.35/32"},
	} {
		if err := invalid.validate(); err == nil {
			t.Fatal("tampered service policy validated")
		}
	}
}

func TestEgressPolicyValidationRejectsTamperedCanonicalPolicy(t *testing.T) {
	for _, policy := range []EgressPolicy{
		{blockCIDRs: "8.8.8.8/32"},
		{blockCIDRs: baselineBlockedCIDRs + ",8.8.8.8/32,8.8.8.0/24"},
		{blockCIDRs: baselineBlockedCIDRs + ",-8.8.8.8/32"},
	} {
		if err := policy.validate(); err == nil {
			t.Fatalf("tampered policy validated: %q", policy.blockCIDRs)
		}
	}
}

func TestRunTaskRejectsMissingEgressPolicyBeforeAllocation(t *testing.T) {
	allocated := false
	started := false
	deps := dependencies{
		process: processStarterFunc(func(int) (managedProcess, error) {
			started = true
			return nil, nil
		}),
		allocatePort: func() (int, error) {
			allocated = true
			return 9222, nil
		},
	}
	if _, err := runTaskWithDependencies(context.Background(), Config{}, deps, validTask()); err == nil {
		t.Fatal("Run accepted a zero-value egress policy")
	}
	if allocated || started {
		t.Fatalf("invalid policy reached allocation/start: allocated=%t started=%t", allocated, started)
	}
}

func TestCommandStarterRejectsMissingEgressPolicy(t *testing.T) {
	starter := commandStarter{binary: "/nonexistent/lightpanda"}
	if _, err := starter.Start(9222); err == nil {
		t.Fatal("command starter accepted a zero-value egress policy")
	}
}

func TestCommandStarterHasExactBoundedEgressArguments(t *testing.T) {
	policy := defaultEgressPolicy()
	command, err := (commandStarter{binary: "lightpanda", egressPolicy: policy}).buildCommand(
		9222,
		&boundedBuffer{limit: maxProcessLogBytes},
	)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"lightpanda", "serve",
		"--host", "127.0.0.1",
		"--port", "9222",
		"--log-level", "error",
		"--cdp-max-connections", "2",
		"--cdp-max-pending-connections", "1",
		"--http-max-concurrent", "8",
		"--http-max-host-open", "4",
		"--http-connect-timeout", "5000",
		"--http-max-response-size", "8388608",
		"--ws-max-concurrent", "1",
		"--block-private-networks",
		"--block-cidrs", policy.blockCIDRs,
	}
	if !reflect.DeepEqual(command.Args, want) {
		t.Fatalf("Lightpanda argv = %q, want %q", command.Args, want)
	}
	blockCIDRFlags := 0
	for index, argument := range command.Args {
		if argument == "--block-cidrs" {
			blockCIDRFlags++
			if index+1 >= len(command.Args) || strings.HasPrefix(command.Args[index+1], "-") || strings.Contains(command.Args[index+1], ",-") {
				t.Fatalf("default --block-cidrs contains an allow exemption: %q", command.Args)
			}
		}
		if strings.Contains(strings.ToLower(argument), "proxy") {
			t.Fatalf("Lightpanda argv contains proxy configuration: %q", command.Args)
		}
	}
	if blockCIDRFlags != 1 {
		t.Fatalf("--block-cidrs count = %d, want 1", blockCIDRFlags)
	}
}
