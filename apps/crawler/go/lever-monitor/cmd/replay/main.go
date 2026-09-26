package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	lever "github.com/colophon-group/jobseek/apps/crawler/go/lever-monitor"
)

func main() {
	body, err := io.ReadAll(io.LimitReader(os.Stdin, 64<<20+1))
	if err != nil || len(body) > 64<<20 {
		fmt.Fprintln(os.Stderr, "invalid Lever replay input")
		os.Exit(1)
	}
	jobs, count, err := lever.ParsePage(body)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	result := struct {
		Jobs  []lever.Job `json:"jobs"`
		Count int         `json:"count"`
	}{jobs, count}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
