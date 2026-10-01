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
	stage := len(os.Args) == 2 && os.Args[1] == "--stage-ownership"
	inspect := len(os.Args) == 2 && os.Args[1] == "--inspect-ownership"
	if len(os.Args) != 1 && !identity && !health && !stage && !inspect {
		fmt.Fprintln(os.Stderr, "use no arguments, --identity, --health, --stage-ownership or --inspect-ownership")
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
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if stage || inspect {
		config, err := worker.ReadOwnershipAdminConfig(os.Getenv, revision, inspect)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary ownership environment rejected")
			os.Exit(1)
		}
		result, err := worker.RunOwnershipAdmin(ctx, config)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary ownership operation rejected")
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(result) != nil {
			os.Exit(1)
		}
		return
	}
	config, err := worker.ReadRuntimeConfig(os.Getenv, revision)
	if err != nil {
		fmt.Fprintln(os.Stderr, "ordinary worker environment rejected")
		os.Exit(1)
	}
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
