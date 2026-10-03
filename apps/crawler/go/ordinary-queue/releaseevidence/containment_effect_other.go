//go:build !linux

package releaseevidence

import "os/exec"

func bindContainmentChild(*exec.Cmd) error {
	return reject("Linux containment child required")
}
