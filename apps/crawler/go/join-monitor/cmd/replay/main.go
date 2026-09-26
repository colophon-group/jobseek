package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	join "github.com/colophon-group/jobseek/apps/crawler/go/join-monitor"
)

func main() {
	var slug string
	var first bool
	flag.StringVar(&slug, "slug", "", "configured JOIN slug")
	flag.BoolVar(&first, "first", true, "whether this is the first page")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	input, err := io.ReadAll(io.LimitReader(os.Stdin, (8<<20)+1))
	if err != nil || len(input) > 8<<20 {
		fmt.Fprintln(os.Stderr, "JOIN replay input exceeded 8 MiB")
		os.Exit(1)
	}
	page, err := join.ParsePage(input, slug, first)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(page); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
