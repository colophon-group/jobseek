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

	pinpoint "github.com/colophon-group/jobseek/apps/crawler/go/pinpoint-monitor"
)

func main() {
	var tenant string
	var parseStdin bool
	flag.BoolVar(&parseStdin, "parse-stdin", false, "parse a retained API response without networking")
	flag.StringVar(&tenant, "tenant", "", "configured Pinpoint tenant")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	if parseStdin {
		if tenant != "" {
			fmt.Fprintln(os.Stderr, "parse-stdin cannot fetch")
			os.Exit(2)
		}
		body, err := io.ReadAll(io.LimitReader(os.Stdin, (64<<20)+1))
		if err != nil || len(body) > 64<<20 {
			fmt.Fprintln(os.Stderr, "invalid retained body")
			os.Exit(1)
		}
		inventory, err := pinpoint.Parse(body)
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
	result, err := pinpoint.FetchTenant(ctx, tenant)
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
