//go:build linux

package b0producer

import (
	"net"
	"syscall"
)

func peerUID(connection *net.UnixConn) (uint32, error) {
	raw, err := connection.SyscallConn()
	if err != nil {
		return 0, err
	}
	var credentials *syscall.Ucred
	var failed error
	if err := raw.Control(func(fd uintptr) {
		credentials, failed = syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	}); err != nil {
		return 0, err
	}
	if failed != nil {
		return 0, failed
	}
	if credentials == nil {
		return 0, ErrAuthority
	}
	return credentials.Uid, nil
}
