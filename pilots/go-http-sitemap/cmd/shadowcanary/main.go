package main

import (
	"context"
	"encoding/json"
	"os"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/canary"
)

const productionManifest = "/canary/production.json"

var (
	sourceCommit  string
	imageIdentity string
)

func main() {
	os.Exit(run())
}

func run() int {
	started := time.Now()
	report := canary.FailureReport(sourceCommit, imageIdentity, "internal", started)
	if len(os.Args) != 1 {
		report = canary.FailureReport(sourceCommit, imageIdentity, "arguments_rejected", started)
	} else if !canary.ValidSourceIdentity(sourceCommit, imageIdentity) {
		report = canary.FailureReport(sourceCommit, imageIdentity, "source_identity_invalid", started)
	} else if manifest, manifestSHA256, err := canary.LoadManifest(productionManifest); err != nil {
		report = canary.FailureReport(sourceCommit, imageIdentity, "manifest_invalid", started)
	} else {
		ctx, cancel := context.WithTimeout(context.Background(), 70*time.Second)
		report = canary.Run(ctx, manifest, manifestSHA256, sourceCommit, imageIdentity)
		cancel()
	}
	report.RunDurationMillis = time.Since(started).Milliseconds()

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
