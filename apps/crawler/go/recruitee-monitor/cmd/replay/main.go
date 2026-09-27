package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	recruitee "github.com/colophon-group/jobseek/apps/crawler/go/recruitee-monitor"
)

func main() {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, (64<<20)+1))
	if err != nil || len(input) > 64<<20 {
		fmt.Fprintln(os.Stderr, "Recruitee replay input exceeded 64 MiB")
		os.Exit(1)
	}
	inventory, err := recruitee.Parse(input)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
