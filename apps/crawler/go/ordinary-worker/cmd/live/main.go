package main

import (
	"context"
	"encoding/json"
	"fmt"
	worker "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-worker"
	"os"
	"os/signal"
	"syscall"
)

// Populated only by the protected release build; never read from runtime env.
var sourceRevision string

func main() {
	identity := len(os.Args) == 2 && os.Args[1] == "--identity"
	health := len(os.Args) == 2 && os.Args[1] == "--health"
	if len(os.Args) != 1 && !identity && !health {
		fmt.Fprintln(os.Stderr, "use no arguments, --identity or --health")
		os.Exit(2)
	}
	revision, err := worker.InstalledBuildRevision(sourceRevision)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ordinary worker build identity rejected")
		os.Exit(1)
	}
	if identity {
		value, err := worker.Identity(sourceRevision)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary worker build identity rejected")
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(value) != nil {
			os.Exit(1)
		}
		return
	}
	config, err := worker.ReadRuntimeConfig(os.Getenv, revision)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ordinary worker environment rejected")
		os.Exit(1)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if health {
		err = worker.CheckHealth(ctx, config)
	} else {
		err = worker.Run(ctx, config)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "ordinary worker failed")
		os.Exit(1)
	}
}
