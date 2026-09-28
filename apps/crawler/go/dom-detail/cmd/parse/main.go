package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"

	dom "github.com/colophon-group/jobseek/apps/crawler/go/dom-detail"
)

func main() {
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "configuration must arrive on stdin")
		os.Exit(2)
	}
	const maxInput = 64 << 20
	body, err := io.ReadAll(io.LimitReader(os.Stdin, maxInput+1))
	if err != nil || len(body) > maxInput {
		fmt.Fprintln(os.Stderr, "invalid bounded configuration")
		os.Exit(2)
	}
	var input struct {
		Mode          string        `json:"mode"`
		HTML          string        `json:"html"`
		Config        dom.Object    `json:"config"`
		URL           *string       `json:"url"`
		Elements      []dom.Element `json:"elements"`
		Steps         []dom.Object  `json:"steps"`
		Start         int           `json:"start"`
		IncludeHidden bool          `json:"include_hidden"`
		IncludeHeader bool          `json:"include_header_content"`
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err = decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, "invalid configuration")
		os.Exit(2)
	}
	var trailing any
	if err = decoder.Decode(&trailing); err != io.EOF {
		fmt.Fprintln(os.Stderr, "trailing configuration")
		os.Exit(2)
	}
	var result any
	switch input.Mode {
	case "", "parse":
		result, err = dom.Parse(input.HTML, input.Config, input.URL)
	case "flatten":
		result, err = dom.Flatten(input.HTML, input.IncludeHidden, input.IncludeHeader)
	case "walk":
		var fields dom.Object
		var cursor int
		fields, cursor, err = dom.WalkSteps(input.Elements, input.Steps, input.Start)
		result = dom.Object{"fields": fields, "cursor": cursor}
	default:
		err = errors.New("unsupported extraction mode")
	}
	if err != nil {
		result = dom.Object{"error": err.Error()}
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(result); encodeErr != nil {
		os.Exit(1)
	}
	if err != nil {
		os.Exit(1)
	}
}
