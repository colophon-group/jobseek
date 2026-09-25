package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	successfactorsrss "github.com/colophon-group/jobseek/apps/crawler/go/successfactors-rss-monitor"
)

func main() {
	jobs, items, err := successfactorsrss.ParseReader(io.LimitReader(os.Stdin, (256<<20)+1))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(struct {
		Jobs  []successfactorsrss.Job `json:"jobs"`
		Items int                     `json:"items"`
	}{Jobs: jobs, Items: items}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
