package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/resident"
)

const maxEvidenceLineBytes = 32 << 10

var (
	sourceCommit  string
	imageIdentity string
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout))
}

func run(args []string, output io.Writer) int {
	started := time.Now()
	duration, err := parseDuration(args)
	if err != nil {
		_ = writeJSONLine(output, failureReport("arguments_rejected", 0, started))
		return 1
	}
	if !resident.ValidSourceIdentity(sourceCommit, imageIdentity) {
		_ = writeJSONLine(output, failureReport("source_identity_invalid", duration, started))
		return 1
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	report := resident.Run(ctx, started, resident.DefaultConfig(duration), sourceCommit, imageIdentity, func(snapshot resident.Snapshot) error {
		return writeJSONLine(output, snapshot)
	})
	if err := writeJSONLine(output, report); err != nil {
		return 1
	}
	if report.Status != "succeeded" {
		return 1
	}
	return 0
}

func parseDuration(args []string) (time.Duration, error) {
	if len(args) != 2 || args[0] != "--duration-minutes" {
		return 0, errors.New("arguments rejected")
	}
	minutes, err := strconv.Atoi(args[1])
	if err != nil {
		return 0, errors.New("arguments rejected")
	}
	switch minutes {
	case 30, 120, 240:
		return time.Duration(minutes) * time.Minute, nil
	default:
		return 0, errors.New("arguments rejected")
	}
}

func failureReport(kind string, duration time.Duration, started time.Time) resident.Report {
	return resident.Report{
		SchemaVersion:     resident.SchemaVersion,
		Kind:              "final",
		SourceCommit:      sourceCommit,
		ImageIdentity:     imageIdentity,
		Status:            "failed",
		ErrorKind:         kind,
		RunDurationMillis: time.Since(started).Milliseconds(),
		ConfiguredMillis:  duration.Milliseconds(),
	}
}

func writeJSONLine(output io.Writer, value any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	if len(encoded) == 0 || len(encoded) > maxEvidenceLineBytes {
		return errors.New("evidence line rejected")
	}
	encoded = append(encoded, '\n')
	for len(encoded) > 0 {
		written, writeErr := output.Write(encoded)
		if writeErr != nil {
			return writeErr
		}
		if written <= 0 || written > len(encoded) {
			return io.ErrShortWrite
		}
		encoded = encoded[written:]
	}
	return nil
}
