//go:build !linux

package b0producer

import "net"

func peerUID(*net.UnixConn) (uint32, error) { return 0, ErrAuthority }
