package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	worker "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-worker"
)

// Bound by the immutable image build, never adopted from runtime environment.
var sourceRevision string

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "ordinary worker command rejected")
		os.Exit(1)
	}
}

func run() error {
	command := "run"
	if len(os.Args) == 2 {
		command = os.Args[1]
	} else if len(os.Args) != 1 {
		return worker.ErrStartup
	}
	revision, err := worker.InstalledBuildRevision(sourceRevision)
	if err != nil {
		return err
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	encode := func(value any, err error) error {
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(value)
	}
	switch command {
	case "--proxy-preflight":
		message, err := worker.ProxyPreflight(os.Getenv)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(os.Stdout, message)
		return err
	case "--identity":
		return encode(worker.Identity(sourceRevision))
	case "--stage-ownership", "--inspect-ownership":
		c, err := worker.ReadOwnershipAdminConfig(os.Getenv, revision, command == "--inspect-ownership")
		if err != nil {
			return err
		}
		return encode(worker.RunOwnershipAdmin(ctx, c))
	case "--activate-first-ownership", "--retire-first-ownership":
		op := "activate"
		if command == "--retire-first-ownership" {
			op = "retire"
		}
		c, err := worker.ReadFirstOwnershipAdminConfig(os.Getenv, revision, op)
		if err != nil {
			log.Print("ordinary worker first ownership stage: configuration")
			return err
		}
		return encode(worker.RunFirstOwnershipAdmin(ctx, c))
	case "run", "--health":
		c, err := worker.ReadRuntimeConfig(os.Getenv, revision)
		if err != nil {
			log.Print("ordinary worker startup stage: configuration")
			return err
		}
		if command == "--health" {
			return worker.CheckHealth(ctx, c)
		}
		return worker.Run(ctx, c)
	default:
		if port, ok := strings.CutPrefix(command, "--worker-health="); ok {
			return worker.CheckWorkerHealth(ctx, port)
		}
		return worker.ErrStartup
	}
}
