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

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
)

func main() {
	var boardURL, slug string
	var parsePage, first bool
	flag.StringVar(&boardURL, "board-url", "", "configured JOIN board URL")
	flag.StringVar(&slug, "slug", "", "configured JOIN company slug")
	flag.BoolVar(&parsePage, "parse-page", false, "parse a captured page from stdin without HTTP")
	flag.BoolVar(&first, "first", true, "whether a captured page is the first page")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	if parsePage {
		body, err := io.ReadAll(io.LimitReader(os.Stdin, (16<<20)+1))
		if err != nil || len(body) > 16<<20 {
			fmt.Fprintln(os.Stderr, "JOIN replay input exceeded 16 MiB")
			os.Exit(1)
		}
		page, err := join.ParsePage(body, slug, first)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if err := json.NewEncoder(os.Stdout).Encode(page); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	result, err := join.FetchBoard(ctx, boardURL, slug)
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
