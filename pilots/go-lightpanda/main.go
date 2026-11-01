//go:build !densitybench

package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

func main() {
	args := os.Args[1:]
	if len(args) > 0 && args[0] == pinnedProxyChildFlag {
		if execPinnedProxyChild(args[1:]) != nil {
			os.Exit(1)
		}
		return
	}
	if len(args) > 0 && args[0] == configuredBrowserProxyCheckFlag {
		if len(args) != 2 {
			os.Exit(1)
		}
		if checkBrowserProxySettings(configuredBrowserProxyFile, args[1]) != nil {
			os.Exit(1)
		}
		_, _ = fmt.Fprintln(os.Stdout, "browser proxy settings valid")
		return
	}
	if len(args) > 0 && args[0] == runtimeV1ChildIsolationCheckFlag {
		if len(args) != 2 {
			os.Exit(1)
		}
		if err := attestRuntimeV1ChildIsolation(args[1]); err != nil {
			_, _ = fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(args) > 0 && args[0] == runtimeV1ChildIsolationProbeFlag {
		if err := runRuntimeV1ChildIsolationProbe(args[1:]); err != nil {
			_, _ = fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(args) > 0 && args[0] == runtimeV1ServiceProbeNoClient {
		if len(args) != 4 {
			_, _ = fmt.Fprintln(os.Stderr, "runtime-v1 probe arguments are invalid")
			os.Exit(1)
		}
		if err := probeRuntimeV1ServiceWithoutClient(args[1], args[2], args[3]); err != nil {
			// Probe errors are fixed categories, never addresses or certificate data.
			_, _ = fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(args) > 0 && args[0] == runtimeV1ServiceFlag {
		ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		config, err := runtimeV1ServiceConfigFromArgs(args[1:])
		if err == nil {
			err = attestRuntimeV1ChildIsolation(config.PrivateKeyPath)
		}
		if err == nil {
			proxies, proxyErr := readBrowserProxies(configuredBrowserProxyFile)
			if proxyErr != nil {
				cancel()
				os.Exit(1)
			}
			execution, executionErr := newRuntimeV1ServiceExecution(Config{
				Binary:         os.Getenv("LIGHTPANDA_BIN"),
				EgressPolicy:   config.serviceEgressPolicy.egressPolicy,
				proxyEndpoints: proxies,
			}, nil)
			if executionErr != nil {
				err = executionErr
			} else {
				err = runRuntimeV1Service(ctx, config, execution)
			}
		}
		cancel()
		if err != nil {
			os.Exit(1)
		}
		return
	}
	if len(args) > 0 && args[0] == runtimeV1StdioFlag {
		ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
		adapter, err := lightpandaadapter.NewRenderOnly(
			runtimeV1Runner{config: Config{Binary: os.Getenv("LIGHTPANDA_BIN"), EgressPolicy: defaultEgressPolicy()}},
		)
		exitCode := 1
		if err != nil {
			exitCode = runRuntimeV1StdioMode(ctx, args, runtimeV1Input{}, os.Stdout, nil)
		} else {
			exitCode = runRuntimeV1StdioMode(ctx, args, runtimeV1OwnedInput(os.Stdin), os.Stdout, adapter)
		}
		cancel()
		os.Exit(exitCode)
	}
	os.Exit(runCLI(args, os.Stdout))
}
