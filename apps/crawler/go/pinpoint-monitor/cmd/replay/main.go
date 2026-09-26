package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	pinpoint "github.com/colophon-group/jobseek/apps/crawler/go/pinpoint-monitor"
)

func main() {
	input, err := io.ReadAll(io.LimitReader(os.Stdin, (16<<20)+1))
	if err != nil || len(input) > 16<<20 {
		fmt.Fprintln(os.Stderr, "Pinpoint replay input exceeded 16 MiB")
		os.Exit(1)
	}
	inventory, err := pinpoint.Parse(input)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(inventory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
