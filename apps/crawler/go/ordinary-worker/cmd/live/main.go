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
	releaseFiles := len(os.Args) == 2 && os.Args[1] == "--verify-release-files"
	releaseSpecs := len(os.Args) == 2 && os.Args[1] == "--capture-deploy-specs"
	stage := len(os.Args) == 2 && os.Args[1] == "--stage-ownership"
	inspect := len(os.Args) == 2 && os.Args[1] == "--inspect-ownership"
	cold := ""
	if len(os.Args) == 2 {
		cold = worker.ColdAdminOperation(os.Args[1])
	}
	if len(os.Args) != 1 && !identity && !health && !releaseFiles && !releaseSpecs && !stage && !inspect && cold == "" {
		fmt.Fprintln(os.Stderr, "ordinary worker argument rejected")
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
	if releaseSpecs {
		config, err := worker.ReadReleaseSpecsConfig(os.Getenv, revision)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary release spec environment rejected")
			os.Exit(1)
		}
		result, err := worker.RunReleaseSpecs(ctx, config)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary release spec capture rejected")
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(result) != nil {
			os.Exit(1)
		}
		return
	}
	if releaseFiles {
		config, err := worker.ReadReleaseFilesConfig(os.Getenv, revision)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary release file environment rejected")
			os.Exit(1)
		}
		result, err := worker.RunReleaseFiles(ctx, config)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary release file verification rejected")
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(result) != nil {
			os.Exit(1)
		}
		return
	}
	if cold != "" {
		config, err := worker.ReadColdAdminConfig(os.Getenv, revision, cold)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary cold coordinator environment rejected")
			os.Exit(1)
		}
		result, err := worker.RunColdAdmin(ctx, config)
		if err != nil {
			fmt.Fprintln(os.Stderr, "ordinary cold coordinator operation rejected")
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(result) != nil {
			os.Exit(1)
		}
		return
	}
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
