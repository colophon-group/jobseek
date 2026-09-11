//go:build unix && !linux && !densitybench

package main

import "errors"

const (
	runtimeV1ChildIsolationCheckFlag = "--runtime-v1-child-isolation-check"
	runtimeV1ChildIsolationProbeFlag = "--runtime-v1-child-isolation-probe"
)

func attestRuntimeV1ChildIsolation(string) error {
	return errors.New("runtime-v1 child isolation requires Linux")
}

func runRuntimeV1ChildIsolationProbe([]string) error {
	return errors.New("runtime-v1 child isolation requires Linux")
}
