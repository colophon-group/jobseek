package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	recruitee "github.com/colophon-group/jobseek/apps/crawler/go/recruitee-monitor"
)

func main() {
	var tenant string
	var apiBase string
	flag.StringVar(&apiBase, "api-base", "", "configured Recruitee API origin")
	var parseStdin bool
	flag.BoolVar(&parseStdin, "parse-stdin", false, "parse a retained API response without networking")
	flag.StringVar(&tenant, "tenant", "", "configured Recruitee tenant")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	if parseStdin {
		if tenant != "" || apiBase != "" {
			fmt.Fprintln(os.Stderr, "parse-stdin cannot fetch")
			os.Exit(2)
		}
		body, err := io.ReadAll(io.LimitReader(os.Stdin, (64<<20)+1))
		if err != nil || len(body) > 64<<20 {
			fmt.Fprintln(os.Stderr, "invalid retained body")
			os.Exit(1)
		}
		inventory, err := recruitee.Parse(body)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := json.NewEncoder(os.Stdout).Encode(inventory); err != nil {
			os.Exit(1)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	var result recruitee.FetchResult
	var err error
	if apiBase != "" {
		if tenant != "" {
			fmt.Fprintln(os.Stderr, "choose tenant or api-base")
			os.Exit(2)
		}
		result, err = recruitee.FetchBase(ctx, apiBase)
	} else {
		result, err = recruitee.FetchTenant(ctx, tenant)
	}
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
