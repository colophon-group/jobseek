package worker

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"net"
	"net/netip"
	"time"
)

var ErrUnsafeURL = errors.New("direct HTTP target rejected")

//go:embed ssrf_networks.json
var ssrfNetworks []byte

type addressPolicy struct{ private, exceptions, always []netip.Prefix }

var v4Policy, v6Policy = loadAddressPolicies()

func loadAddressPolicies() (addressPolicy, addressPolicy) {
	var data map[string]struct {
		Private, Exceptions []string
		Always              []string `json:"always_blocked"`
	}
	if json.Unmarshal(ssrfNetworks, &data) != nil {
		panic("invalid compiled address policy")
	}
	parse := func(values []string) []netip.Prefix {
		out := make([]netip.Prefix, 0, len(values))
		for _, value := range values {
			out = append(out, netip.MustParsePrefix(value))
		}
		return out
	}
	build := func(key string) addressPolicy {
		value, ok := data[key]
		if !ok || len(value.Private) == 0 || len(value.Always) == 0 {
			panic("missing compiled address policy")
		}
		return addressPolicy{parse(value.Private), parse(value.Exceptions), parse(value.Always)}
	}
	return build("v4"), build("v6")
}

func blockedAddress(address netip.Addr) bool {
	if !address.IsValid() {
		return true
	}
	if address.Is4In6() {
		return blockedAddress(address.Unmap())
	}
	address = address.WithZone("")
	policy := v6Policy
	if address.Is4() {
		policy = v4Policy
	}
	contains := func(prefixes []netip.Prefix) bool {
		for _, prefix := range prefixes {
			if prefix.Contains(address) {
				return true
			}
		}
		return false
	}
	return contains(policy.always) || contains(policy.private) && !contains(policy.exceptions)
}

func validatedAddresses(addresses []netip.Addr) ([]netip.Addr, error) {
	var public []netip.Addr
	for _, address := range addresses {
		if !address.IsValid() {
			return nil, ErrUnsafeURL
		}
		if blockedAddress(address) {
			if address.Is6() && address.IsLinkLocalUnicast() && address.Zone() == "" {
				continue // unscoped, unroutable AAAA, only with another public answer
			}
			return nil, ErrUnsafeURL
		}
		public = append(public, address)
	}
	if len(public) == 0 {
		return nil, ErrUnsafeURL
	}
	return public, nil
}

type lookupIPFunc func(context.Context, string) ([]netip.Addr, error)

func resolvePublic(ctx context.Context, host string, lookup lookupIPFunc) ([]netip.Addr, error) {
	for attempt := 0; ; attempt++ {
		addresses, err := lookup(ctx, host)
		if err == nil {
			return validatedAddresses(addresses)
		}
		var failure *net.DNSError
		if attempt == 2 || !errors.As(err, &failure) || !failure.IsTemporary || failure.IsTimeout {
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			return nil, ErrUnsafeURL
		}
		delay := time.NewTimer(time.Duration(attempt+1) * 50 * time.Millisecond)
		select {
		case <-ctx.Done():
			delay.Stop()
			return nil, ctx.Err()
		case <-delay.C:
		}
	}
}
