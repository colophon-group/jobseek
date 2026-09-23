//go:build !linux

package main

import (
	"errors"
	"net"
)

func producerPeerUID(*net.UnixConn) (uint32, error) {
	return 0, errors.New("SO_PEERCRED is unavailable")
}
