//go:build unix && !linux

package main

import (
	"errors"
	"syscall"
)

func lightpandaProcessAttributes(isolateChild bool) (*syscall.SysProcAttr, error) {
	if isolateChild {
		return nil, errors.New("isolated service execution requires Linux")
	}
	return &syscall.SysProcAttr{Setpgid: true}, nil
}
