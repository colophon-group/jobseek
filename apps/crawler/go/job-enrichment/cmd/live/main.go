package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	enrichment "github.com/colophon-group/jobseek/apps/crawler/go/job-enrichment"
)

func main() {
	data := flag.String("data-dir", "/app/data", "read-only taxonomy directory")
	flag.Parse()
	if flag.NArg() != 0 {
		fmt.Fprintln(os.Stderr, "unexpected arguments")
		os.Exit(2)
	}
	matcher, err := enrichment.Load(*data)
	if err != nil {
		fmt.Fprintln(os.Stderr, "taxonomy load failed:", err)
		os.Exit(1)
	}
	output := json.NewEncoder(os.Stdout)
	// This is an internal JSONL pipe, never embedded in HTML. Avoid multiplying
	// normalized descriptions by JSON's optional HTML-safe escaping.
	output.SetEscapeHTML(false)
	if err = output.Encode(map[string]any{"ready": true, "protocol": 1}); err != nil {
		os.Exit(1)
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 64<<10), 16<<20)
	for scanner.Scan() {
		var request enrichment.Request
		if err = json.Unmarshal(scanner.Bytes(), &request); err != nil {
			fmt.Fprintln(os.Stderr, "invalid bounded request")
			os.Exit(1)
		}
		response, err := matcher.Process(request)
		if err != nil {
			response.Error = err.Error()
		}
		if output.Encode(response) != nil {
			os.Exit(1)
		}
	}
	if scanner.Err() != nil {
		fmt.Fprintln(os.Stderr, "input exceeds bound or read failed")
		os.Exit(1)
	}
}
