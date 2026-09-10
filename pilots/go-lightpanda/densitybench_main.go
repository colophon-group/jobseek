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

const densityStartGatePath = "/tmp/controller-start"

func main() { os.Exit(runDensityCLI(os.Args[1:], os.Stdout)) }

func runDensityCLI(args []string, output io.Writer) int {
	flags := flag.NewFlagSet("go-lightpanda-density", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	workloadPath := flags.String("workload", "", "")
	concurrency := flags.Int("concurrency", 0, "")
	sourceCommit := flags.String("source-commit", "", "")
	imageIdentity := flags.String("image-identity", "", "")
	startGate := flags.String("start-gate", "", "")
	started := time.Now()
	if flags.Parse(args) != nil || flags.NArg() != 0 || *workloadPath == "" || *startGate != densityStartGatePath {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "arguments_invalid")
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if waitDensityStartGate(ctx, *startGate) != nil {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "start_gate")
	}
	workload, workloadSHA, err := loadDensityWorkload(*workloadPath)
	if err != nil {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "manifest_invalid")
	}
	adapter, err := lightpandaadapter.New(runtimeV1Runner{config: Config{Binary: os.Getenv("LIGHTPANDA_BIN")}}, densityRawPrivacy{})
	if err != nil {
		return writeDensityFailure(output, started, *concurrency, *sourceCommit, *imageIdentity, "adapter_initialization")
	}
	report := runDensityBenchmark(ctx, workload, workloadSHA, *concurrency, *sourceCommit, *imageIdentity, adapter)
	if encodeDensityReport(output, report) != nil {
		return 1
	}
	if !report.Succeeded {
		return 1
	}
	return 0
}

func waitDensityStartGate(ctx context.Context, path string) error {
	if ctx == nil || path == "" {
		return errDensityConfig
	}
	ticker := time.NewTicker(10 * time.Millisecond)
	defer ticker.Stop()
	for {
		info, err := os.Lstat(path)
		if err == nil {
			metadata, ok := info.Sys().(*syscall.Stat_t)
			if !info.Mode().IsRegular() || !ok || int(metadata.Uid) != os.Geteuid() {
				return errDensityConfig
			}
			return nil
		}
		if !os.IsNotExist(err) {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
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
