package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	ashby "github.com/colophon-group/jobseek/apps/crawler/go/ashby-monitor"
)

func main() {
	body, err := io.ReadAll(io.LimitReader(os.Stdin, 64<<20+1))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	result, err := ashby.Parse(body)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
