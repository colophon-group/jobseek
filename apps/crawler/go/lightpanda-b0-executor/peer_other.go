//go:build !linux

package executor

import "net"

func executorPeerUID(*net.UnixConn) (uint32, error) { return 0, ErrProtocol }
