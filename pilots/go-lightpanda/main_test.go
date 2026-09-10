package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"
)

func TestRunCLIReturnsFailureWhenSuccessOutputIsDowngraded(t *testing.T) {
	result := validResult()
	result.HTML = strings.Repeat("\x01", maxHTMLBytes)
	if err := validateResult(validTask(), result); err != nil {
		t.Fatalf("expansion-heavy result should pass field bounds: %v", err)
	}

	var output bytes.Buffer
	exitCode := runCLIWithRunner(
		[]string{"https://example.test/jobs", "document.title"},
		&output,
		func(context.Context, Config, Task) (Result, error) { return result, nil },
	)
	if exitCode != 1 {
		t.Fatalf("exit code = %d, want 1", exitCode)
	}
	var response cliResponse
	if err := json.Unmarshal(output.Bytes(), &response); err != nil {
		t.Fatalf("decode downgraded response: %v", err)
	}
	if response.OK || response.Error != "JSON output exceeded the pilot's bounded output limit" {
		t.Fatalf("unexpected downgraded response: %+v", response)
	}
}

func TestRunCLIReturnsFailureWhenSuccessOutputCannotBeWritten(t *testing.T) {
	tests := []struct {
		name   string
		writer io.Writer
	}{
		{name: "writer error", writer: errorWriter{err: errors.New("output closed")}},
		{name: "short write", writer: shortWriter{}},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			exitCode := runCLIWithRunner(
				[]string{"https://example.test/jobs", "document.title"},
				test.writer,
				func(context.Context, Config, Task) (Result, error) { return validResult(), nil },
			)
			if exitCode != 1 {
				t.Fatalf("exit code = %d, want 1", exitCode)
			}
		})
	}
}

type errorWriter struct {
	err error
}

func (writer errorWriter) Write([]byte) (int, error) {
	return 0, writer.err
}

type shortWriter struct{}

func (shortWriter) Write(data []byte) (int, error) {
	return len(data) - 1, nil
}
