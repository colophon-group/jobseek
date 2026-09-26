package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	workable "github.com/colophon-group/jobseek/apps/crawler/go/workable-monitor"
)

func main() {
	var slug, llmsPath, jobsPath, publicPath string
	flag.StringVar(&slug, "slug", "", "selected Workable account slug")
	flag.StringVar(&llmsPath, "llms", "", "exact captured llms.txt")
	flag.StringVar(&jobsPath, "jobs", "", "exact captured jobs.md")
	flag.StringVar(&publicPath, "public", "", "optional exact captured public API response")
	flag.Parse()
	if slug == "" || llmsPath == "" || flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "slug, llms capture, and no positional arguments are required")
		os.Exit(2)
	}
	llms, err := os.ReadFile(llmsPath)
	if err != nil || len(llms) > 64<<20 {
		fmt.Fprintln(os.Stderr, "invalid llms capture", err)
		os.Exit(1)
	}
	var jobs, public []byte
	if jobsPath != "" {
		jobs, err = os.ReadFile(jobsPath)
		if err != nil || len(jobs) > 64<<20 {
			fmt.Fprintln(os.Stderr, "invalid jobs capture", err)
			os.Exit(1)
		}
	}
	if publicPath != "" {
		public, err = os.ReadFile(publicPath)
		if err != nil || len(public) > 64<<20 {
			fmt.Fprintln(os.Stderr, "invalid public capture", err)
			os.Exit(1)
		}
	}
	result, err := workable.ProjectFallback(slug, llms, jobs, public)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
