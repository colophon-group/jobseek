package main

import (
	"errors"
	"fmt"
	"net/netip"
	"slices"
	"strings"
)

// baselineBlockedCIDRs is the version-controlled destination deny baseline.
//
// It is deliberately a conservative superset of the IANA non-global
// special-purpose registries: it also blocks multicast, reserved and
// transition space. In particular, 192.0.0.0/24, 2001::/23, and the
// complement of 2000::/3 contain limited globally reachable exceptions. The
// pilot accepts that compatibility cost to avoid gaps and must be explicitly
// revised if one of those destinations becomes necessary.
// Source registries (reviewed 2026-09-10):
// https://www.iana.org/assignments/iana-ipv4-special-registry/
// https://www.iana.org/assignments/iana-ipv6-special-registry/
// https://www.iana.org/assignments/ipv6-address-space/
const baselineBlockedCIDRs = "0.0.0.0/8,10.0.0.0/8,100.64.0.0/10,127.0.0.0/8,169.254.0.0/16,172.16.0.0/12,192.0.0.0/24,192.0.2.0/24,192.88.99.0/24,192.168.0.0/16,198.18.0.0/15,198.51.100.0/24,203.0.113.0/24,224.0.0.0/4,240.0.0.0/4,::/3,2001::/23,2001:db8::/32,2002::/16,3fff::/20,4000::/2,8000::/1"

const (
	maxEgressCIDRCount = 128
	maxEgressCIDRBytes = 16 << 10
	maxSingleCIDRBytes = 64
)

// EgressPolicy is immutable after construction. Its only representation is a
// canonical, sorted, comma-separated deny list. Fields stay private so runtime
// request data cannot construct or amend a policy.
type EgressPolicy struct {
	blockCIDRs string
}

// runtimeV1ServiceEgressPolicy is proof that a service policy was constructed
// from a bounded deployment inventory, not from the registry baseline alone.
// Both fields stay private and validation reconstructs them from the startup
// inventory, so the baseline-only defaultEgressPolicy cannot qualify a
// network service.
type runtimeV1ServiceEgressPolicy struct {
	egressPolicy             EgressPolicy
	canonicalDeploymentCIDRs string
}

func defaultEgressPolicy() EgressPolicy {
	return mustNewEgressPolicy(nil)
}

// newEgressPolicy is reserved for trusted startup configuration. Additional
// entries can name an exact host or project CIDR that is not already covered by
// the baseline. They are never accepted from a browser request. This is not a
// public-service constructor: that later layer must require and verify a
// nonempty deployment-specific address inventory and external enforcement.
func newEgressPolicy(additional []string) (EgressPolicy, error) {
	baseline := strings.Split(baselineBlockedCIDRs, ",")
	if len(additional) > maxEgressCIDRCount-len(baseline) {
		return EgressPolicy{}, errors.New("additional CIDR deny list exceeds its entry limit")
	}
	entries := make([]string, 0, len(baseline)+len(additional))
	entries = append(entries, baseline...)
	entries = append(entries, additional...)
	canonical, err := canonicalBlockedCIDRs(entries)
	if err != nil {
		return EgressPolicy{}, err
	}
	return EgressPolicy{blockCIDRs: canonical}, nil
}

// newRuntimeV1ServiceEgressPolicy reconciles the trusted startup inventory
// with the registry baseline. Entries already contained by the baseline stay
// in the inventory proof but are not duplicated in Lightpanda's deny list.
// Every other inventory entry must be an uncovered globally routed prefix.
func newRuntimeV1ServiceEgressPolicy(inventory []string) (runtimeV1ServiceEgressPolicy, error) {
	canonicalInventory, uncovered, err := canonicalDeploymentDenyInventory(inventory)
	if err != nil {
		return runtimeV1ServiceEgressPolicy{}, err
	}
	policy, err := newEgressPolicy(uncovered)
	if err != nil {
		return runtimeV1ServiceEgressPolicy{}, errors.New("deployment deny inventory could not qualify the service policy")
	}
	qualified := runtimeV1ServiceEgressPolicy{
		egressPolicy:             policy,
		canonicalDeploymentCIDRs: canonicalInventory,
	}
	if err := qualified.validate(); err != nil {
		return runtimeV1ServiceEgressPolicy{}, err
	}
	return qualified, nil
}

func (policy runtimeV1ServiceEgressPolicy) validate() error {
	var inventory []string
	if policy.canonicalDeploymentCIDRs != "" {
		inventory = strings.Split(policy.canonicalDeploymentCIDRs, ",")
	}
	expected, err := newRuntimeV1ServiceEgressPolicyUnchecked(inventory)
	if err != nil {
		return err
	}
	if policy.canonicalDeploymentCIDRs != expected.canonicalDeploymentCIDRs ||
		policy.egressPolicy.blockCIDRs != expected.egressPolicy.blockCIDRs {
		return errors.New("runtime-v1 service egress policy does not cover its deployment inventory")
	}
	return policy.egressPolicy.validate()
}

func newRuntimeV1ServiceEgressPolicyUnchecked(inventory []string) (runtimeV1ServiceEgressPolicy, error) {
	canonicalInventory, uncovered, err := canonicalDeploymentDenyInventory(inventory)
	if err != nil {
		return runtimeV1ServiceEgressPolicy{}, err
	}
	policy, err := newEgressPolicy(uncovered)
	if err != nil {
		return runtimeV1ServiceEgressPolicy{}, errors.New("deployment deny inventory could not qualify the service policy")
	}
	return runtimeV1ServiceEgressPolicy{
		egressPolicy:             policy,
		canonicalDeploymentCIDRs: canonicalInventory,
	}, nil
}

func canonicalDeploymentDenyInventory(entries []string) (string, []string, error) {
	baseline := strings.Split(baselineBlockedCIDRs, ",")
	if len(entries) == 0 {
		return "", nil, errors.New("runtime-v1 service deployment deny inventory is required")
	}
	if len(entries) > maxEgressCIDRCount-len(baseline) {
		return "", nil, errors.New("deployment deny inventory exceeds its entry limit")
	}

	prefixes := make([]netip.Prefix, 0, len(entries))
	inputBytes := 0
	for _, entry := range entries {
		inputBytes += len(entry)
		if entry == "" || len(entry) > maxSingleCIDRBytes || strings.TrimSpace(entry) != entry || strings.HasPrefix(entry, "-") {
			return "", nil, errors.New("deployment deny inventory contains a malformed or non-canonical CIDR")
		}
		prefix, err := netip.ParsePrefix(entry)
		if err != nil || !prefix.IsValid() || prefix.Addr().Is4In6() || prefix.Masked() != prefix || prefix.String() != entry {
			return "", nil, errors.New("deployment deny inventory contains a malformed or non-canonical CIDR")
		}
		prefixes = append(prefixes, prefix)
	}
	if inputBytes+len(entries)-1 > maxEgressCIDRBytes {
		return "", nil, errors.New("deployment deny inventory exceeds its byte limit")
	}

	slices.SortFunc(prefixes, func(left, right netip.Prefix) int {
		if order := left.Addr().Compare(right.Addr()); order != 0 {
			return order
		}
		return left.Bits() - right.Bits()
	})
	for index, prefix := range prefixes {
		for _, previous := range prefixes[:index] {
			if previous == prefix {
				return "", nil, errors.New("deployment deny inventory contains a duplicate CIDR")
			}
		}
	}

	baselinePrefixes := make([]netip.Prefix, 0, len(baseline))
	for _, entry := range baseline {
		baselinePrefixes = append(baselinePrefixes, netip.MustParsePrefix(entry))
	}
	canonical := make([]string, len(prefixes))
	uncovered := make([]string, 0, len(prefixes))
	for index, prefix := range prefixes {
		canonical[index] = prefix.String()
		covered := false
		for _, blocked := range baselinePrefixes {
			if prefixCoveredBy(prefix, blocked) {
				covered = true
				break
			}
			if prefixesOverlap(prefix, blocked) {
				return "", nil, errors.New("deployment deny inventory partially overlaps the registry baseline")
			}
		}
		if covered {
			continue
		}
		if !prefix.Addr().IsGlobalUnicast() {
			return "", nil, errors.New("deployment deny inventory contains an uncovered non-global CIDR")
		}
		uncovered = append(uncovered, prefix.String())
	}
	if len(uncovered) == 0 {
		return "", nil, errors.New("deployment deny inventory requires a globally routable uncovered CIDR")
	}
	return strings.Join(canonical, ","), uncovered, nil
}

func prefixCoveredBy(prefix, covering netip.Prefix) bool {
	return prefix.Addr().BitLen() == covering.Addr().BitLen() &&
		prefix.Bits() >= covering.Bits() && covering.Contains(prefix.Addr())
}

func prefixesOverlap(left, right netip.Prefix) bool {
	if left.Addr().BitLen() != right.Addr().BitLen() {
		return false
	}
	return left.Contains(right.Addr()) || right.Contains(left.Addr())
}

func mustNewEgressPolicy(additional []string) EgressPolicy {
	policy, err := newEgressPolicy(additional)
	if err != nil {
		panic(fmt.Sprintf("invalid built-in Lightpanda egress policy: %v", err))
	}
	return policy
}

func (policy EgressPolicy) validate() error {
	if policy.blockCIDRs == "" {
		return errors.New("Lightpanda egress policy is required")
	}
	entries := strings.Split(policy.blockCIDRs, ",")
	canonical, err := canonicalBlockedCIDRs(entries)
	if err != nil {
		return fmt.Errorf("invalid Lightpanda egress policy: %w", err)
	}
	if canonical != policy.blockCIDRs {
		return errors.New("Lightpanda egress policy is not canonical")
	}
	present := make(map[string]struct{}, len(entries))
	for _, entry := range entries {
		present[entry] = struct{}{}
	}
	for _, required := range strings.Split(baselineBlockedCIDRs, ",") {
		if _, ok := present[required]; !ok {
			return fmt.Errorf("Lightpanda egress policy omitted required CIDR %q", required)
		}
	}
	return nil
}

func canonicalBlockedCIDRs(entries []string) (string, error) {
	if len(entries) == 0 {
		return "", errors.New("CIDR deny list is empty")
	}
	if len(entries) > maxEgressCIDRCount {
		return "", errors.New("CIDR deny list exceeds its entry limit")
	}
	prefixes := make([]netip.Prefix, 0, len(entries))
	for _, entry := range entries {
		if entry == "" || len(entry) > maxSingleCIDRBytes || strings.TrimSpace(entry) != entry || strings.HasPrefix(entry, "-") {
			return "", fmt.Errorf("CIDR %q is empty, whitespace-padded, or an allow exemption", entry)
		}
		prefix, err := netip.ParsePrefix(entry)
		if err != nil || !prefix.IsValid() || prefix.Addr().Is4In6() || prefix.Masked() != prefix || prefix.String() != entry {
			return "", fmt.Errorf("CIDR %q is malformed or non-canonical", entry)
		}
		prefixes = append(prefixes, prefix)
	}
	slices.SortFunc(prefixes, func(left, right netip.Prefix) int {
		if order := left.Addr().Compare(right.Addr()); order != 0 {
			return order
		}
		return left.Bits() - right.Bits()
	})
	for index, prefix := range prefixes {
		for _, previous := range prefixes[:index] {
			if previous == prefix {
				return "", fmt.Errorf("CIDR %q is duplicated", prefix)
			}
			if previous.Addr().BitLen() == prefix.Addr().BitLen() && previous.Contains(prefix.Addr()) {
				return "", fmt.Errorf("CIDRs %q and %q overlap", previous, prefix)
			}
		}
	}
	canonical := make([]string, len(prefixes))
	for index, prefix := range prefixes {
		canonical[index] = prefix.String()
	}
	joined := strings.Join(canonical, ",")
	if len(joined) > maxEgressCIDRBytes {
		return "", errors.New("CIDR deny list exceeds its byte limit")
	}
	return joined, nil
}
