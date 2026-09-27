package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	ashby "github.com/colophon-group/jobseek/apps/crawler/go/ashby-monitor"
)

func main() {
	var token string
	var resolveOnly bool
	flag.StringVar(&token, "token", "", "configured Ashby board token")
	flag.BoolVar(&resolveOnly, "resolve-only", false, "print the canonical endpoint without network access")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	if resolveOnly {
		endpoint, err := ashby.TokenURL(token)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		fmt.Println(endpoint)
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := ashby.FetchToken(ctx, token)
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
