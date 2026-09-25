package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"os"
	"time"

	"github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/boundedhttp"
	"github.com/colophon-group/jobseek/apps/crawler/go/sitemap-monitor/sitemap"
)

type output struct {
	URLs          []string `json:"urls"`
	Truncated     bool     `json:"truncated"`
	Requests      int      `json:"requests"`
	Responses     int      `json:"responses"`
	WireAttempts  int      `json:"wire_attempts"`
	ResponseBytes int64    `json:"response_bytes"`
	Error         string   `json:"error,omitempty"`
}

func main() { os.Exit(run()) }

func run() int {
	var rawURL string
	flag.StringVar(&rawURL, "sitemap-url", "", "explicit HTTPS sitemap URL")
	flag.Parse()
	out := output{URLs: []string{}}
	if flag.NArg() != 0 || rawURL == "" {
		out.Error = "invalid_arguments"
		return emit(out)
	}
	client, err := boundedhttp.New(boundedhttp.Config{
		RequestTimeout:           20 * time.Second,
		MaxDecodedBodyBytes:      50 * 1024 * 1024,
		MaxRequests:              3,
		MaxAggregateDecodedBytes: 55 * 1024 * 1024,
	})
	if err != nil {
		out.Error = "client_config"
		return emit(out)
	}
	defer client.Close()
	runner, err := sitemap.New(client, sitemap.Config{
		SitemapURL:       rawURL,
		MaxURLs:          50_000,
		MaxIndexChildren: 1,
		RequireURLSet:    true,
	})
	if err != nil {
		out.Error = "sitemap_config"
		return emit(out)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 70*time.Second)
	defer cancel()
	result, err := runner.Run(ctx)
	out.URLs = result.URLs
	out.Truncated = result.Truncated
	out.Requests = result.TransportMetrics.Requests
	out.Responses = result.TransportMetrics.Responses
	out.WireAttempts = result.TransportMetrics.WireAttempts
	out.ResponseBytes = result.TransportMetrics.DecodedBytes + result.TransportMetrics.StatusBodyBytes
	if err != nil {
		out.Error = "sitemap_failure"
		var typed *sitemap.Error
		var retry *sitemap.RetryExhaustedError
		var transport *boundedhttp.Error
		switch {
		case errors.As(err, &typed):
			out.Error = string(typed.Kind)
		case errors.As(err, &retry):
			out.Error = "retry_exhausted"
		case errors.As(err, &transport):
			out.Error = string(transport.Kind)
		}
	}
	return emit(out)
}

func emit(out output) int {
	if err := json.NewEncoder(os.Stdout).Encode(out); err != nil || out.Error != "" {
		return 1
	}
	return 0
}
