//go:build unix && !linux

package main

import "syscall"

func lightpandaProcessAttributes() *syscall.SysProcAttr {
	return &syscall.SysProcAttr{Setpgid: true}
}
