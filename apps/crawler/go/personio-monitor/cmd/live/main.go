package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	personio "github.com/colophon-group/jobseek/apps/crawler/go/personio-monitor"
)

func main() {
	var slug, domain, language, backfillRaw string
	flag.StringVar(&slug, "slug", "", "Personio company slug")
	flag.StringVar(&domain, "domain", "", "preferred Personio domain: de or com")
	flag.StringVar(&language, "language", "en", "primary XML language")
	flag.StringVar(&backfillRaw, "backfill-languages", "de", "comma-separated alternate XML languages")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	backfill := []string{}
	if backfillRaw != "" {
		backfill = strings.Split(backfillRaw, ",")
	}
	parent, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(parent, 5*time.Minute)
	defer cancel()
	result, err := personio.FetchFeed(ctx, slug, domain, language, backfill)
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
