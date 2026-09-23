//go:build linux

package main

import (
	"net"
	"syscall"
)

func producerPeerUID(connection *net.UnixConn) (uint32, error) {
	raw, err := connection.SyscallConn()
	if err != nil {
		return 0, err
	}
	var credentials *syscall.Ucred
	var credentialErr error
	if err := raw.Control(func(descriptor uintptr) {
		credentials, credentialErr = syscall.GetsockoptUcred(int(descriptor), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	}); err != nil {
		return 0, err
	}
	if credentialErr != nil {
		return 0, credentialErr
	}
	return credentials.Uid, nil
}
