package main

import (
	"bytes"
	"encoding/json"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/colophon-group/jobseek/pilots/go-http-sitemap/resident"
)

func TestParseDurationAllowsOnlyReviewedChoices(t *testing.T) {
	for _, minutes := range []string{"30", "120", "240"} {
		duration, err := parseDuration([]string{"--duration-minutes", minutes})
		if err != nil {
			t.Fatalf("%s rejected: %v", minutes, err)
		}
		if duration.String() == "" {
			t.Fatalf("%s produced empty duration", minutes)
		}
	}
	for _, args := range [][]string{nil, {"30"}, {"--duration", "30"}, {"--duration-minutes", "1"}, {"--duration-minutes", "241"}, {"--duration-minutes", "30", "extra"}} {
		if _, err := parseDuration(args); err == nil {
			t.Fatalf("arguments unexpectedly accepted: %#v", args)
		}
	}
}

func TestInvalidArgumentsEmitOneSanitizedFinalLine(t *testing.T) {
	originalCommit, originalIdentity := sourceCommit, imageIdentity
	t.Cleanup(func() {
		sourceCommit, imageIdentity = originalCommit, originalIdentity
	})
	sourceCommit = "0123456789abcdef0123456789abcdef01234567"
	imageIdentity = "ghcr.io/colophon-group/jobseek-go-sitemap-resident-shadow:sha-" + sourceCommit
	var output bytes.Buffer
	if status := run([]string{"--duration-minutes", "1"}, &output); status != 1 {
		t.Fatalf("status = %d, want 1", status)
	}
	lines := strings.Split(strings.TrimSpace(output.String()), "\n")
	if len(lines) != 1 {
		t.Fatalf("lines = %d, want 1", len(lines))
	}
	var report resident.Report
	if err := json.Unmarshal([]byte(lines[0]), &report); err != nil {
		t.Fatal(err)
	}
	if report.Kind != "final" || report.Status != "failed" || report.ErrorKind != "arguments_rejected" {
		t.Fatalf("unexpected report: %+v", report)
	}
}

func TestWriteJSONLineRejectsOversizeAndShortWrites(t *testing.T) {
	if err := writeJSONLine(&bytes.Buffer{}, strings.Repeat("x", maxEvidenceLineBytes)); err == nil {
		t.Fatal("oversized evidence unexpectedly accepted")
	}
	if err := writeJSONLine(shortWriter{}, map[string]bool{"ok": true}); err != io.ErrShortWrite {
		t.Fatalf("short write error = %v", err)
	}
}

type shortWriter struct{}

func (shortWriter) Write([]byte) (int, error) { return 0, nil }

func TestFailureReportDoesNotExposeRawCause(t *testing.T) {
	report := failureReport("arguments_rejected", 30*time.Minute, time.Now())
	encoded, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(encoded), "127.0.0.1") || strings.Contains(string(encoded), "fixture.invalid") {
		t.Fatalf("failure report leaked workload data: %s", encoded)
	}
}
