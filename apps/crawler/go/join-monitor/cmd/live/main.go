package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
)

func main() {
	var boardURL, slug string
	flag.StringVar(&boardURL, "board-url", "", "configured JOIN board URL")
	flag.StringVar(&slug, "slug", "", "configured JOIN company slug")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := join.FetchBoard(ctx, boardURL, slug)
	if err != nil {
		result.Error = err.Error()
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(result); encodeErr != nil {
		fmt.Fprintln(os.Stderr, encodeErr)
		os.Exit(1)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
