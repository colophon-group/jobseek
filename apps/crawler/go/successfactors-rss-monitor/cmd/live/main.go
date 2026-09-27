package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	successfactorsrss "github.com/colophon-group/jobseek/apps/crawler/go/successfactors-rss-monitor"
)

func main() {
	var feedURL string
	var parseStdin bool
	flag.StringVar(&feedURL, "feed-url", "", "configured SuccessFactors RSS feed")
	flag.BoolVar(&parseStdin, "parse-stdin", false, "parse a retained RSS feed offline")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	if parseStdin {
		if feedURL != "" {
			fmt.Fprintln(os.Stderr, "parse-stdin cannot fetch a feed")
			os.Exit(2)
		}
		jobs, items, err := successfactorsrss.ParseReader(os.Stdin)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := json.NewEncoder(os.Stdout).Encode(struct {
			Jobs  []successfactorsrss.Job `json:"jobs"`
			Items int                     `json:"items"`
		}{jobs, items}); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	encoder := json.NewEncoder(os.Stdout)
	summary, err := successfactorsrss.FetchFeed(parent, feedURL, func(job successfactorsrss.Job) error {
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
