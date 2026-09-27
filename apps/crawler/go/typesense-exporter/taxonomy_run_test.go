package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

func TestTaxonomyCLIEmitsOneRedactedRecord(t *testing.T) {
	for _, kind := range []string{"ready", "not_ready", "error", "interrupted"} {
		t.Run(kind, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if kind == "interrupted" {
				cancel()
			}
			var output bytes.Buffer
			code := taxonomyCLI(ctx, &output, func(context.Context) (map[string]any, error) {
				if kind == "error" || kind == "interrupted" {
					return nil, errors.New("secret-credential SQL source payload")
				}
				return map[string]any{"status": kind}, nil
			})
			expected := 1
			if kind == "ready" {
				expected = 0
			}
			if kind == "interrupted" {
				expected = 130
			}
			if code != expected {
				t.Fatalf("exit %d", code)
			}
			if strings.Count(output.String(), "\n") != 1 || strings.Contains(output.String(), "secret") {
				t.Fatal("unredacted or multiple records")
			}
			var record map[string]any
			if err := json.Unmarshal(output.Bytes(), &record); err != nil {
				t.Fatal(err)
			}
		})
	}
}
