package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

func main() {
	var format string
	flag.StringVar(&format, "format", "json", "Workable detail format: json or markdown")
	flag.Parse()
	if flag.NArg() != 0 || format != "json" && format != "markdown" {
		fmt.Fprintln(os.Stderr, "invalid Workable detail projection arguments")
		os.Exit(2)
	}
	body, err := io.ReadAll(io.LimitReader(os.Stdin, 64<<20+1))
	if err != nil || len(body) > 64<<20 {
		fmt.Fprintln(os.Stderr, "invalid or oversized Workable detail body")
		os.Exit(1)
	}
	var content workable.DetailContent
	if format == "markdown" {
		content = workable.ProjectMarkdownDetail(body)
	} else {
		content, err = workable.ProjectDetail(body)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	if err := json.NewEncoder(os.Stdout).Encode(content); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
