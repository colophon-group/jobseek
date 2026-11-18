//go:build linux

package main

import "syscall"

const (
	lightpandaControllerUID = 10001
	lightpandaControllerGID = 10001
	lightpandaChildUID      = 10002
	lightpandaChildGID      = 10002
)

func lightpandaProcessAttributes(isolateChild bool) (*syscall.SysProcAttr, error) {
	attributes := &syscall.SysProcAttr{
		Setpgid:   true,
		Pdeathsig: syscall.SIGKILL,
	}
	if isolateChild {
		attributes.Credential = &syscall.Credential{
			Uid:    lightpandaChildUID,
			Gid:    lightpandaChildGID,
			Groups: []uint32{lightpandaChildGID},
		}
	}
	return attributes, nil
}
