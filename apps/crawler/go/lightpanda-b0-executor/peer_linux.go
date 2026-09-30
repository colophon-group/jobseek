//go:build linux

package executor

import (
	"net"
	"syscall"
)

func executorPeerUID(conn *net.UnixConn) (uint32, error) {
	raw, err := conn.SyscallConn()
	if err != nil {
		return 0, ErrProtocol
	}
	var uid uint32
	var peerError error
	err = raw.Control(func(fd uintptr) {
		credentials, err := syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
		if err != nil {
			peerError = err
			return
		}
		uid = credentials.Uid
	})
	if err != nil || peerError != nil {
		return 0, ErrProtocol
	}
	return uid, nil
}
