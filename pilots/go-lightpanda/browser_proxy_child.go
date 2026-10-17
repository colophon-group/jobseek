package main

import (
	"path/filepath"
	"strconv"
	"strings"
)

const pinnedProxyChildFlag = "--runtime-v1-pinned-proxy-child"

// This is a process-owned trampoline, never a renderer request operation.
// The controller constructs its executable and argument vector after dropping
// the child's identity and capabilities through the existing setpriv boundary.
func execPinnedProxyChild(args []string) error {
	if len(args) < 3 || len(args) > 66 {
		return errBrowserProxyNetworkPolicy
	}
	port, err := strconv.ParseUint(args[0], 10, 16)
	if err != nil || port == 0 || strconv.FormatUint(port, 10) != args[0] ||
		!filepath.IsAbs(args[1]) || filepath.Clean(args[1]) != args[1] {
		return errBrowserProxyNetworkPolicy
	}
	total := 0
	for _, arg := range args {
		if len(arg) > 8192 || strings.ContainsAny(arg, "\x00\r\n") {
			return errBrowserProxyNetworkPolicy
		}
		total += len(arg)
	}
	if total > 64<<10 {
		return errBrowserProxyNetworkPolicy
	}
	return execPinnedProxyChildRestricted(uint16(port), args[1], args[2:])
}
