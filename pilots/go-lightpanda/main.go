package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"strings"
	"syscall"
)

const maxCLIOutputBytes = 2 << 20

type cliResponse struct {
	OK     bool    `json:"ok"`
	Result *Result `json:"result,omitempty"`
	Error  string  `json:"error,omitempty"`
}

type taskRunner func(context.Context, Config, Task) (Result, error)

func main() {
	os.Exit(runCLI(os.Args[1:], os.Stdout))
}

func runCLI(args []string, output io.Writer) int {
	return runCLIWithRunner(args, output, runTask)
}

func runCLIWithRunner(args []string, output io.Writer, runner taskRunner) int {
	if len(args) != 2 {
		_ = writeCLIResponse(output, cliResponse{OK: false, Error: "usage: go-lightpanda <url> <synchronous-expression>"})
		return 2
	}
	binary := os.Getenv("LIGHTPANDA_BIN")
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	result, err := runner(ctx, Config{Binary: binary}, Task{
		URL: args[0],
		Evaluation: &TaskEvaluation{
			Expression:     args[1],
			MaxResultBytes: maxExpressionResult,
		},
	})
	if err != nil {
		_ = writeCLIResponse(output, cliResponse{OK: false, Error: boundedError(err)})
		return 1
	}
	if err := writeCLIResponse(output, cliResponse{OK: true, Result: &result}); err != nil {
		return 1
	}
	return 0
}

func writeCLIResponse(output io.Writer, response cliResponse) error {
	encoded, err := encodeCLIResponse(response)
	if err == nil && len(encoded) <= maxCLIOutputBytes {
		return writeCLIBytes(output, encoded)
	}

	var downgradeErr error
	fallbackMessage := "JSON output could not be encoded"
	if err != nil {
		downgradeErr = fmt.Errorf("encode CLI response: %w", err)
	} else {
		fallbackMessage = "JSON output exceeded the pilot's bounded output limit"
		downgradeErr = fmt.Errorf("CLI response contains %d bytes, limit is %d", len(encoded), maxCLIOutputBytes)
	}
	fallback, fallbackErr := encodeCLIResponse(cliResponse{OK: false, Error: fallbackMessage})
	if fallbackErr != nil {
		return errors.Join(downgradeErr, fmt.Errorf("encode CLI fallback: %w", fallbackErr))
	}
	return errors.Join(downgradeErr, writeCLIBytes(output, fallback))
}

func encodeCLIResponse(response cliResponse) ([]byte, error) {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(response); err != nil {
		return nil, err
	}
	return encoded.Bytes(), nil
}

func writeCLIBytes(output io.Writer, encoded []byte) error {
	written, err := output.Write(encoded)
	if err != nil {
		return fmt.Errorf("write CLI response: %w", err)
	}
	if written != len(encoded) {
		return fmt.Errorf("write CLI response: wrote %d of %d bytes: %w", written, len(encoded), io.ErrShortWrite)
	}
	return nil
}

func boundedError(err error) string {
	const limit = 2 << 10
	message := strings.ToValidUTF8(err.Error(), "?")
	if len(message) <= limit {
		return message
	}
	return fmt.Sprintf("%s...", message[:limit-3])
}
