package main

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

func TestB0CutoverRollbackEntriesMatchActualOriginalPlan(t *testing.T) {
	body, err := os.ReadFile("testdata/python_b0_cutover_plan_entries.json")
	var corpus struct {
		Identity b0CutoverIdentity
		Vectors  []struct {
			Name        string
			Row         *b0PlanPosting
			Stored      b0PlanStored
			Accepted    bool
			Task, Entry map[string]any
		}
	}
	decoder := json.NewDecoder(strings.NewReader(string(body)))
	decoder.UseNumber()
	if err != nil || decoder.Decode(&corpus) != nil || len(corpus.Vectors) != 27 {
		t.Fatal("original full planner entry corpus unavailable", len(corpus.Vectors))
	}
	for _, c := range corpus.Vectors {
		t.Run(c.Name, func(t *testing.T) {
			task, entry, err := b0PlanRollbackEntry("posting", c.Stored, c.Row, corpus.Identity)
			if (err == nil) != c.Accepted {
				t.Fatal("original planning acceptance differs", err)
			}
			if err != nil {
				return
			}
			for _, pair := range [][2]map[string]any{{task, c.Task}, {entry, c.Entry}} {
				got, err := b0task.CanonicalJSON(pair[0], true)
				if err != nil {
					t.Fatal(err)
				}
				want, err := b0task.CanonicalJSON(pair[1], true)
				if err != nil {
					t.Fatal(err)
				}
				if string(got) != string(want) {
					t.Fatal("complete original rollback entry differs", string(got), string(want))
				}
			}
		})
	}
}
