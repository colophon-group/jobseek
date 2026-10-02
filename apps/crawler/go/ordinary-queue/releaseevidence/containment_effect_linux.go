//go:build linux

package releaseevidence

import (
	"os/exec"
	"syscall"
)

func bindContainmentChild(cmd *exec.Cmd) error {
	// Do not leave an unbounded CLI child after coordinator SIGKILL. Engine
	// requests already accepted may still finish; retries use the same retained
	// IDs and never start anything. The later restoration phase must reconcile
	// those effects before permitting restarts.
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGKILL}
	return nil
}
