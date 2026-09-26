package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	personio "github.com/colophon-group/jobseek/apps/crawler/go/personio-monitor"
)

func main() {
	var slug, domain string
	flag.StringVar(&slug, "slug", "", "Personio company slug")
	flag.StringVar(&domain, "domain", "", "Personio domain suffix: de or com")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected positional argument")
		os.Exit(2)
	}
	jobs := []personio.Job{}
	positions, count, err := personio.ParseReader(os.Stdin, slug, domain, func(job personio.Job) error {
		jobs = append(jobs, job)
		return nil
	})
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(struct {
		Positions int            `json:"positions"`
		Count     int            `json:"count"`
		Jobs      []personio.Job `json:"jobs"`
	}{positions, count, jobs}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
