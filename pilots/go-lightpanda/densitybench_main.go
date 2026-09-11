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

type densityProtocolPhase func(context.Context) error

const (
	densityInitialMarkerPath = "/tmp/controller-initial"
	densityFinalMarkerPath   = "/tmp/controller-final"
)

func main() { os.Exit(runDensityCLI(os.Args[1:], os.Stdout)) }

func runDensityCLI(args []string, output io.Writer) int {
	return runDensityCLIWithProtocol(args, output, densityWaitInitialRelease, densityWaitFinalRelease)
}

func runDensityCLIWithProtocol(args []string, output io.Writer, waitInitial, waitFinal densityProtocolPhase) int {
	flags := flag.NewFlagSet("go-lightpanda-density", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	workloadPath := flags.String("workload", "", "")
	concurrency := flags.Int("concurrency", 0, "")
	sourceCommit := flags.String("source-commit", "", "")
	imageIdentity := flags.String("image-identity", "", "")
	parseErr := flags.Parse(args)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if waitInitial == nil || waitInitial(ctx) != nil || ctx.Err() != nil {
		return 1
	}
	started := time.Now()
	if parseErr != nil || flags.NArg() != 0 || *workloadPath == "" {
		report := densityFailureReport(started, *concurrency, *sourceCommit, *imageIdentity, "arguments_invalid")
		return finishDensityCLI(ctx, output, report, 2, waitFinal)
	}

	workload, workloadSHA, err := loadDensityWorkload(*workloadPath)
	if err != nil {
		report := densityFailureReport(started, *concurrency, *sourceCommit, *imageIdentity, "manifest_invalid")
		return finishDensityCLI(ctx, output, report, 2, waitFinal)
	}
	adapter, err := lightpandaadapter.New(runtimeV1Runner{
		config: Config{Binary: os.Getenv("LIGHTPANDA_BIN"), EgressPolicy: defaultEgressPolicy()},
		run:    densityFixtureTaskRunner,
	}, densityRawPrivacy{})
	if err != nil {
		report := densityFailureReport(started, *concurrency, *sourceCommit, *imageIdentity, "adapter_initialization")
		return finishDensityCLI(ctx, output, report, 2, waitFinal)
	}
	report := runDensityBenchmark(ctx, workload, workloadSHA, *concurrency, *sourceCommit, *imageIdentity, adapter)
	if !report.Succeeded {
		return finishDensityCLI(ctx, output, report, 1, waitFinal)
	}
	return finishDensityCLI(ctx, output, report, 0, waitFinal)
}

func densityWaitInitialRelease(ctx context.Context) error {
	return densityWaitForMarkerSignal(ctx, densityInitialMarkerPath, syscall.SIGUSR1)
}

func densityWaitFinalRelease(ctx context.Context) error {
	return densityWaitForMarkerSignal(ctx, densityFinalMarkerPath, syscall.SIGUSR2)
}

func densityWaitForMarkerSignal(ctx context.Context, path string, releaseSignal os.Signal) error {
	if ctx == nil || path == "" || releaseSignal == nil {
		return errDensityConfig
	}
	released := make(chan os.Signal, 1)
	signal.Notify(released, releaseSignal)
	defer signal.Stop(released)
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := densityCreateMarkerAt(path); err != nil {
		return err
	}
	select {
	case <-released:
		return densityRemoveMarkerAt(path)
	case <-ctx.Done():
		return ctx.Err()
	}
}

func densityCreateMarkerAt(path string) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	info, statErr := file.Stat()
	closeErr := file.Close()
	if statErr != nil {
		return statErr
	}
	if closeErr != nil || densityValidateMarkerInfo(info) != nil {
		return errDensityConfig
	}
	return nil
}

func densityRemoveMarkerAt(path string) error {
	info, err := os.Lstat(path)
	if err != nil || densityValidateMarkerInfo(info) != nil {
		return errDensityConfig
	}
	return os.Remove(path)
}

func densityValidateMarkerInfo(info os.FileInfo) error {
	if info == nil {
		return errDensityConfig
	}
	metadata, ok := info.Sys().(*syscall.Stat_t)
	if !ok || !info.Mode().IsRegular() || info.Mode().Perm() != 0o600 || info.Size() != 0 ||
		metadata.Nlink != 1 || int(metadata.Uid) != os.Geteuid() || int(metadata.Gid) != os.Getegid() {
		return errDensityConfig
	}
	return nil
}

func densityFailureReport(started time.Time, concurrency int, sourceCommit, imageIdentity, failureID string) densityReport {
	if !densityLowerHex(sourceCommit, 40) {
		sourceCommit = ""
	}
	if !densityImageIdentity(imageIdentity) {
		imageIdentity = ""
	}
	return densityReport{SchemaVersion: 1, ImplementationID: "go-lightpanda", RuntimeID: "go", SourceCommit: sourceCommit, ImageIdentity: imageIdentity, Concurrency: concurrency, FailureID: failureID, ElapsedNS: time.Since(started).Nanoseconds(), Waves: []densityWaveReport{}}
}

func finishDensityCLI(ctx context.Context, output io.Writer, report densityReport, exitCode int, waitFinal densityProtocolPhase) int {
	if waitFinal == nil || waitFinal(ctx) != nil || ctx.Err() != nil {
		return 1
	}
	if encodeDensityReport(output, report) != nil {
		return 1
	}
	return exitCode
}

func encodeDensityReport(output io.Writer, report densityReport) error {
	encoder := json.NewEncoder(output)
	encoder.SetEscapeHTML(false)
	return encoder.Encode(report)
}
