package main

import (
	"context"
	"encoding/json"
	"os"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/benchmark"
)

const fleetManifest = "/canary/fleet.json"

var (
	sourceCommit  string
	imageIdentity string
)

func main() {
	os.Exit(run())
}

func run() int {
	started := time.Now()
	profile := ""
	report := benchmark.FailureReport(profile, sourceCommit, imageIdentity, "internal", started)
	if len(os.Args) != 3 || os.Args[1] != "--profile" {
		report = benchmark.FailureReport(profile, sourceCommit, imageIdentity, "arguments_rejected", started)
	} else {
		profile = os.Args[2]
		if _, ok := benchmark.ConcurrencyForProfile(profile); !ok {
			report = benchmark.FailureReport(profile, sourceCommit, imageIdentity, "arguments_rejected", started)
		} else if !benchmark.ValidSourceIdentity(sourceCommit, imageIdentity) {
			report = benchmark.FailureReport(profile, sourceCommit, imageIdentity, "source_identity_invalid", started)
		} else if manifest, digest, err := benchmark.LoadManifest(fleetManifest); err != nil {
			report = benchmark.FailureReport(profile, sourceCommit, imageIdentity, "manifest_invalid", started)
		} else {
			ctx, cancel := context.WithTimeout(context.Background(), 210*time.Second)
			report = benchmark.Run(ctx, started, manifest, digest, profile, sourceCommit, imageIdentity)
			cancel()
		}
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(true)
	if err := encoder.Encode(report); err != nil {
		return 1
	}
	if report.Status != "succeeded" {
		return 1
	}
	return 0
}
