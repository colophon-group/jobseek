package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"
	"time"
)

func TestB0CutoverPlanValuesMatchActualOriginal(t *testing.T) {
	body, err := os.ReadFile("testdata/python_b0_cutover_plan_values.json")
	var corpus struct {
		Timestamps []struct {
			Timestamp, Seconds string
			First              string `json:"first_time_ready"`
		}
		Guards []struct {
			Raw, Kind, Score string
			Accepted         bool
		}
		Canonical struct {
			Document map[string]any
			Bytes    string
			Digest   string `json:"sha256"`
		}
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&corpus) != nil || len(corpus.Timestamps) != 6 || len(corpus.Guards) < 20 {
		t.Fatal("original plan values unavailable")
	}
	for _, c := range corpus.Timestamps {
		t.Run(c.Timestamp, func(t *testing.T) {
			value, err := time.Parse(time.RFC3339Nano, c.Timestamp)
			if err != nil {
				t.Fatal(err)
			}
			got, err := b0PlanSeconds(value)
			if err != nil || got != c.Seconds {
				t.Fatal("canonical schedule differs", got, err)
			}
			got, err = b0PlanReadyAt(value, true, "")
			if err != nil || got != c.First {
				t.Fatal("first-time schedule differs", got, err)
			}
		})
	}
	identity := b0CutoverIdentity{Namespace: "pilot", Shard: "lightpanda-b0", Epoch: 7}
	for _, c := range corpus.Guards {
		t.Run(c.Raw, func(t *testing.T) {
			kind, score, err := b0PlanBoundGuard(c.Raw, identity, "board", "jobs.example")
			if (err == nil) != c.Accepted {
				t.Fatal("original transfer guard decision differs", err)
			}
			if err == nil && (kind != c.Kind || score != c.Score) {
				t.Fatal("original transfer binding differs")
			}
		})
	}
	encoded, digest, err := b0PlanCanonical(corpus.Canonical.Document)
	if err != nil || string(encoded) != corpus.Canonical.Bytes || digest != corpus.Canonical.Digest {
		t.Fatal("original plan bytes/digest differ", err)
	}
}
