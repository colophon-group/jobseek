package main

import (
	"encoding/json"
	"fmt"
	"os"
)

// The JSON protocol is a dark, offline entry point for comparing the Go
// projection with Python's live exporter. It does not touch a production
// cursor or Typesense collection.
func main() {
	var input struct {
		Row  Row  `json:"row"`
		Maps Maps `json:"maps"`
	}
	decoder := json.NewDecoder(os.Stdin)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	doc, err := project(input.Row, input.Maps)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(doc); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
