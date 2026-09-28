package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	jsonld "github.com/colophon-group/jobseek/apps/crawler/go/jsonld-detail"
	"io"
	"os"
	"os/signal"
	"syscall"
)

func main() {
	parse := flag.Bool("parse", false, "parse already fetched HTML without HTTP")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "use --parse")
		os.Exit(2)
	}
	limit := 64 << 10
	if *parse {
		limit = 64 << 20
	}
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, int64(limit)+1))
	var req jsonld.Request
	if err != nil || len(raw) > limit || json.Unmarshal(raw, &req) != nil {
		fmt.Fprintln(os.Stderr, "invalid or oversized JSON-LD input")
		os.Exit(1)
	}
	if *parse {
		content, err := jsonld.Parse(req.URL, []byte(req.HTML), req.Config)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if json.NewEncoder(os.Stdout).Encode(content) != nil {
			os.Exit(1)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := jsonld.FetchDetail(ctx, req)
	if err != nil {
		result.Error = err.Error()
	}
	if json.NewEncoder(os.Stdout).Encode(result) != nil {
		os.Exit(1)
	}
	if err != nil {
		os.Exit(1)
	}
}
