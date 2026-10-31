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

	teamtailorrss "github.com/colophon-group/jobseek/apps/crawler/go/teamtailor-rss-monitor"
)

func main() {
	var feedURL, fallbackJSON string
	flag.StringVar(&feedURL, "feed-url", "", "configured Teamtailor RSS feed")
	flag.StringVar(&fallbackJSON, "country-fallbacks-json", "", "source-verified location name to country map")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(parent, 5*time.Minute)
	defer cancel()
	var fallbacks map[string]string
	if fallbackJSON != "" {
		if err := json.Unmarshal([]byte(fallbackJSON), &fallbacks); err != nil || len(fallbacks) == 0 || len(fallbacks) > 32 {
			fmt.Fprintln(os.Stderr, "invalid country fallback map")
			os.Exit(2)
		}
	}
	result, err := teamtailorrss.FetchFeedWithCountryFallbacks(ctx, feedURL, fallbacks)
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
