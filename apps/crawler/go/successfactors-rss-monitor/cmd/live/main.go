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

	successfactorsrss "github.com/colophon-group/jobseek/apps/crawler/go/successfactors-rss-monitor"
)

func main() {
	var feedURL string
	flag.StringVar(&feedURL, "feed-url", "", "configured SuccessFactors RSS feed")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(parent, 5*time.Minute)
	defer cancel()
	encoder := json.NewEncoder(os.Stdout)
	summary, err := successfactorsrss.FetchFeed(ctx, feedURL, func(job successfactorsrss.Job) error {
		return encoder.Encode(struct {
			Type string                `json:"type"`
			Job  successfactorsrss.Job `json:"job"`
		}{Type: "job", Job: job})
	})
	if err != nil {
		summary.Error = err.Error()
	}
	if encodeErr := encoder.Encode(summary); encodeErr != nil {
		fmt.Fprintln(os.Stderr, encodeErr)
		os.Exit(1)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
