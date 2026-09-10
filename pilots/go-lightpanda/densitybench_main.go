//go:build densitybench

package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/lightpandaadapter"
)

func main() { os.Exit(runDensityCLI(os.Args[1:], os.Stdout)) }

func runDensityCLI(args []string, output io.Writer) int {
	flags := flag.NewFlagSet("go-lightpanda-density", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	workloadPath := flags.String("workload", "", "")
	concurrency := flags.Int("concurrency", 0, "")
	sourceCommit := flags.String("source-commit", "", "")
	imageIdentity := flags.String("image-identity", "", "")
	started := time.Now()
	if flags.Parse(args) != nil || flags.NArg() != 0 || *workloadPath == "" {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "arguments_invalid")
	}
	workload, workloadSHA, err := loadDensityWorkload(*workloadPath)
	if err != nil {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "manifest_invalid")
	}
	adapter, err := lightpandaadapter.New(runtimeV1Runner{config: Config{Binary: os.Getenv("LIGHTPANDA_BIN")}}, densityRawPrivacy{})
	if err != nil {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "adapter_initialization")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	report := runDensityBenchmark(ctx, workload, workloadSHA, *concurrency, *sourceCommit, *imageIdentity, adapter)
	if encodeDensityReport(output, report) != nil {
		return 1
	}
	if !report.Succeeded {
		return 1
	}
	return 0
}

func writeDensityFailure(output io.Writer, started time.Time, concurrency int, sourceCommit, imageIdentity, failureID string) int {
	if !densityLowerHex(sourceCommit, 40) {
		sourceCommit = ""
	}
	if !densityImageIdentity(imageIdentity) {
		imageIdentity = ""
	}
	report := densityReport{SchemaVersion: 1, ImplementationID: "go-lightpanda", RuntimeID: "go", SourceCommit: sourceCommit, ImageIdentity: imageIdentity, Concurrency: concurrency, FailureID: failureID, ElapsedMS: time.Since(started).Milliseconds(), Waves: []densityWaveReport{}}
	if encodeDensityReport(output, report) != nil {
		return 1
	}
	return 2
}

func encodeDensityReport(output io.Writer, report densityReport) error {
	encoder := json.NewEncoder(output)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(report)
}
