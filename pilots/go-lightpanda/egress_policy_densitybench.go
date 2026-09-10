//go:build densitybench

package main

import (
	"context"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"os/exec"
)

// densityFixtureStarter is present only in the credential-free densitybench
// binary. The benchmark validates a repository-embedded workload, resolves one
// of its fixed .bench.test fixture names, and exempts only that container's
// exact private IPv4 /32. It is intentionally not part of the default binary
// and is not production egress evidence.
type densityFixtureStarter struct {
	binary      string
	fixtureAddr netip.Addr
}

func (starter densityFixtureStarter) Start(port int) (managedProcess, error) {
	args, err := densityFixtureCommandArgs(port, starter.fixtureAddr)
	if err != nil {
		return nil, err
	}
	logs := &boundedBuffer{limit: maxProcessLogBytes}
	command := exec.Command(starter.binary, args...)
	command.Env = append([]string(nil), lightpandaChildEnvironment...)
	command.SysProcAttr = lightpandaProcessAttributes()
	command.Stdout = logs
	command.Stderr = logs
	if err := command.Start(); err != nil {
		return nil, err
	}
	return &commandProcess{command: command, pgid: command.Process.Pid, logs: logs}, nil
}

func densityFixtureCommandArgs(port int, fixtureAddr netip.Addr) ([]string, error) {
	if !fixtureAddr.Is4() || !fixtureAddr.IsPrivate() || fixtureAddr.IsLoopback() || fixtureAddr.IsUnspecified() {
		return nil, errors.New("density fixture must resolve to one exact non-loopback private IPv4 address")
	}
	return fixedLightpandaServeArgs(port, baselineBlockedCIDRs+",-"+fixtureAddr.String()+"/32"), nil
}

func densityFixtureTaskRunner(ctx context.Context, config Config, task Task) (Result, error) {
	config, err := normalizeConfig(config)
	if err != nil {
		return Result{}, errDensityConfig
	}
	parsed, err := url.Parse(task.URL)
	if err != nil || !densityFixtureHostname(parsed.Hostname()) {
		return Result{}, errDensityConfig
	}
	addresses, err := net.DefaultResolver.LookupNetIP(ctx, "ip4", parsed.Hostname())
	if err != nil || len(addresses) != 1 {
		return Result{}, errDensityConfig
	}
	fixtureAddr := addresses[0].Unmap()
	if !fixtureAddr.Is4() || !fixtureAddr.IsPrivate() || fixtureAddr.IsLoopback() || fixtureAddr.IsUnspecified() {
		return Result{}, errDensityConfig
	}
	return runTaskWithDependencies(ctx, config, dependencies{
		process:      densityFixtureStarter{binary: config.Binary, fixtureAddr: fixtureAddr},
		ready:        httpReadyWaiter{interval: defaultReadyInterval},
		executor:     chromedpExecutor{},
		allocatePort: allocateLoopbackPort,
		releasePort:  releaseLoopbackPort,
		portOpen:     loopbackPortOpen,
	}, task)
}

func densityFixtureHostname(host string) bool {
	for index := range densityOriginCount {
		if host == fmt.Sprintf("origin-%d.bench.test", index) {
			return true
		}
	}
	return false
}
