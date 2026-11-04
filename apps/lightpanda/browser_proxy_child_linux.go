//go:build linux

package main

import (
	"runtime"
	"unsafe"

	"golang.org/x/sys/unix"
)

// ABI 4 introduced TCP port rules. Use its exact 16-byte attributes, without
// requesting filesystem restrictions or later optional ABI extensions.
type pinnedProxyRuleset struct{ filesystem, network uint64 }
type pinnedProxyPortRule struct{ access, port uint64 }

func pinnedProxyNetworkABI() (uintptr, error) {
	abi, _, errno := unix.Syscall(unix.SYS_LANDLOCK_CREATE_RULESET, 0, 0, unix.LANDLOCK_CREATE_RULESET_VERSION)
	if errno != 0 || abi < 4 {
		return 0, errBrowserProxyNetworkPolicy
	}
	return abi, nil
}

func execPinnedProxyChildRestricted(port uint16, binary string, args []string) error {
	// Landlock applies to the calling thread and survives exec. The replacement
	// browser and every thread it creates inherit the same restriction.
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	if port == 0 {
		return errBrowserProxyNetworkPolicy
	}
	if _, err := pinnedProxyNetworkABI(); err != nil {
		return err
	}
	attr := pinnedProxyRuleset{network: unix.LANDLOCK_ACCESS_NET_CONNECT_TCP}
	fd, _, errno := unix.Syscall(unix.SYS_LANDLOCK_CREATE_RULESET, uintptr(unsafe.Pointer(&attr)), unsafe.Sizeof(attr), 0)
	runtime.KeepAlive(attr)
	if errno != 0 {
		return errBrowserProxyNetworkPolicy
	}
	defer unix.Close(int(fd))
	rule := pinnedProxyPortRule{access: unix.LANDLOCK_ACCESS_NET_CONNECT_TCP, port: uint64(port)}
	const landlockRuleNetPort = 2
	_, _, errno = unix.Syscall6(unix.SYS_LANDLOCK_ADD_RULE, fd, landlockRuleNetPort, uintptr(unsafe.Pointer(&rule)), 0, 0, 0)
	runtime.KeepAlive(rule)
	if errno != 0 || unix.Prctl(unix.PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != nil {
		return errBrowserProxyNetworkPolicy
	}
	if _, _, errno = unix.Syscall(unix.SYS_LANDLOCK_RESTRICT_SELF, fd, 0, 0); errno != 0 {
		return errBrowserProxyNetworkPolicy
	}
	// Only the existing clean child environment crosses the replacement exec.
	// No proxy endpoint, authentication material or controller secret is passed.
	if unix.Exec(binary, append([]string{binary}, args...), lightpandaChildEnvironment) != nil {
		return errBrowserProxyNetworkPolicy
	}
	return nil
}
