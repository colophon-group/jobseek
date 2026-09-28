package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
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
	body, err := io.ReadAll(io.LimitReader(os.Stdin, (64<<10)+1))
	if err != nil || len(body) > 64<<10 {
		os.Exit(2)
	}
	var input struct {
		URL     string                 `json:"url"`
		Options jsonld.DocumentOptions `json:"options"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err = decoder.Decode(&input); err != nil {
		os.Exit(2)
	}
	var trailing any
	if decoder.Decode(&trailing) != io.EOF {
		os.Exit(2)
	}
	signalCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(signalCtx, 10*time.Minute)
	defer cancel()
	result, err := jsonld.FetchDocument(ctx, input.URL, input.Options)
	if err != nil {
		result.Error = "native public document fetch failed"
	}
	if json.NewEncoder(os.Stdout).Encode(result) != nil {
		os.Exit(1)
	}
	if err != nil {
		os.Exit(1)
	}
}
