package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	executor "github.com/colophon-group/jobseek/apps/crawler/go/lightpanda-b0-executor"
)

func main() {
	health := len(os.Args) == 2 && os.Args[1] == "--health"
	if len(os.Args) != 1 && !health {
		fmt.Fprintln(os.Stderr, "use no arguments or --health")
		os.Exit(2)
	}
	config, err := executor.ReadRuntimeConfig(os.Getenv)
	if err != nil {
		fmt.Fprintln(os.Stderr, "executor environment rejected")
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if health {
		probe, stop := context.WithTimeout(ctx, 3*time.Second)
		defer stop()
		err = executor.CheckHealth(probe, config)
	} else {
		err = executor.Run(ctx, config, "/app/data")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "executor failed")
		os.Exit(1)
	}
}
