//go:build !linux

package main

func execPinnedProxyChildRestricted(uint16, string, []string) error {
	return errBrowserProxyNetworkPolicy
}
