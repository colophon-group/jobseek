package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

func main() {
	var source, token string
	flag.StringVar(&source, "url", "", "canonical Workable job URL")
	flag.StringVar(&token, "token", "", "optional configured Workable account token")
	flag.Parse()
	if source == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "a canonical --url and no positional arguments are required")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 45*time.Second)
	defer cancel()
	result, err := workable.FetchDetail(ctx, source, token)
	if err != nil && result.Error == "" {
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
