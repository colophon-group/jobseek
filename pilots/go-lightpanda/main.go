//go:build !densitybench

package main

import (
	"context"
	"os"
	"os/signal"
	"syscall"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

func main() {
	args := os.Args[1:]
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
