//go:build ignore

// Fixture-only adjacent-version converter. It is never built into crawler runtime.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
)

type conversionCase struct {
	ID      string         `json:"id"`
	Payload map[string]any `json:"payload"`
}

type batch struct {
	Cases []conversionCase `json:"cases"`
}

type loss struct {
	Path   string `json:"path"`
	Reason string `json:"reason"`
}

type result struct {
	ID      string         `json:"id"`
	Losses  []loss         `json:"losses"`
	Payload map[string]any `json:"payload"`
}

type results struct {
	Results []result `json:"results"`
}

func convert(direction string, payload map[string]any) result {
	converted := make(map[string]any, len(payload))
	for key, value := range payload {
		converted[key] = value
	}
	losses := make([]loss, 0)
	if direction == "old_to_new" {
		if _, present := converted["legacy_note"]; present {
			delete(converted, "legacy_note")
			losses = append(losses, loss{
				Path:   "$.legacy_note",
				Reason: "field removed in adjacent test version",
			})
		}
	}
	if direction == "new_to_old" {
		if _, present := converted["future_hint"]; present {
			delete(converted, "future_hint")
			losses = append(losses, loss{
				Path:   "$.future_hint",
				Reason: "field unavailable in adjacent test version",
			})
		}
	}
	return result{Losses: losses, Payload: converted}
}

func main() {
	direction := flag.String("direction", "", "old_to_new or new_to_old")
	flag.Parse()
	if *direction != "old_to_new" && *direction != "new_to_old" {
		fmt.Fprintln(os.Stderr, "invalid --direction")
		os.Exit(2)
	}
	decoder := json.NewDecoder(os.Stdin)
	decoder.UseNumber()
	var input batch
	if err := decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if len(input.Cases) == 0 {
		fmt.Fprintln(os.Stderr, "converter input must contain nonempty cases")
		os.Exit(1)
	}
	output := results{Results: make([]result, 0, len(input.Cases))}
	for _, item := range input.Cases {
		if item.ID == "" || item.Payload == nil {
			fmt.Fprintln(os.Stderr, "converter case must have id and payload")
			os.Exit(1)
		}
		converted := convert(*direction, item.Payload)
		converted.ID = item.ID
		output.Results = append(output.Results, converted)
	}
	encoded, err := json.Marshal(output)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoded = append(encoded, '\n')
	if _, err := os.Stdout.Write(encoded); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
