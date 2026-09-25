package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	teamtailorrss "github.com/colophon-group/jobseek/apps/crawler/go/teamtailor-rss-monitor"
)

func main() {
	body, err := io.ReadAll(io.LimitReader(os.Stdin, (32<<20)+1))
	if err != nil || len(body) > 32<<20 {
		fmt.Fprintln(os.Stderr, "Teamtailor replay input exceeded 32 MiB")
		os.Exit(1)
	}
	jobs, items, err := teamtailorrss.ParsePage(body)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(struct {
		Jobs  []teamtailorrss.Job `json:"jobs"`
		Items int                 `json:"items"`
	}{Jobs: jobs, Items: items}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
