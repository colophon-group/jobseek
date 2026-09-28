package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	smartrecruiters "github.com/colophon-group/jobseek/apps/crawler/go/smartrecruiters-monitor"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "configuration must arrive on stdin")
		os.Exit(2)
	}
	body, err := io.ReadAll(io.LimitReader(os.Stdin, 65537))
	if err != nil || len(body) > 65536 {
		fmt.Fprintln(os.Stderr, "invalid bounded configuration")
		os.Exit(2)
	}
	var input struct {
		Responses      map[string][]smartrecruiters.Object `json:"responses"`
		Posting        smartrecruiters.Object              `json:"posting"`
		Mode           string                              `json:"mode"`
		URL            string                              `json:"url"`
		BoardURL       string                              `json:"board_url"`
		Metadata       smartrecruiters.Object              `json:"metadata"`
		CaptureBoardID string                              `json:"capture_board_id"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err = decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(2)
	}
	var trailing any
	if err = decoder.Decode(&trailing); err != io.EOF {
		fmt.Fprintln(os.Stderr, "trailing configuration")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 20*time.Minute)
	defer cancel()
	var result any
	switch input.Mode {
	case "", "monitor":
		result, err = smartrecruiters.Fetch(ctx, input.BoardURL, input.Metadata)
	case "replay":
		result, err = smartrecruiters.Replay(ctx, input.BoardURL, input.Metadata, input.Responses)
	case "parse-detail":
		result, err = smartrecruiters.ParseDetail(input.Posting)
	case "detail":
		result, err = smartrecruiters.FetchDetailForBoard(ctx, input.URL, input.CaptureBoardID)
	default:
		fmt.Fprintln(os.Stderr, "unknown mode")
		os.Exit(2)
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(result); encodeErr != nil {
		os.Exit(1)
	}
	if err != nil {
		os.Exit(1)
	}
}
