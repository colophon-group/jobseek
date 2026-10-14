package main

import (
	"strings"
	"testing"
)

func TestB0PlanApplyCommandContracts(t *testing.T) {
	digest := strings.Repeat("a", 64)
	for _, args := range [][]string{
		{"plan", "--operation", "activate", "--cohort", "cdom"},
		{"activate", "--cohort", "cdom", "--apply", "--expect-digest", digest},
		{"plan", "--operation", "rollback", "--cohort", "cdom", "--receipt-state", "active", "--source-receipt-sha256", digest},
		{"rollback", "--cohort", "cdom", "--receipt-state", "pending", "--source-receipt-sha256", digest, "--apply", "--expect-digest", digest},
	} {
		if _, err := parseB0PlanOptions(args); err != nil {
			t.Fatal("supported original caller shape refused", args)
		}
	}
	for _, args := range [][]string{
		{}, {"plan"}, {"plan", "--operation", "activate", "--cohort", "c4"},
		{"plan", "--operation", "rollback", "--cohort", "cdom"},
		{"plan", "--operation", "rollback", "--cohort", "cdom", "--receipt-state", "retired", "--source-receipt-sha256", digest},
		{"activate", "--cohort", "cdom", "--expect-digest", digest},
		{"activate", "--cohort", "cdom", "--apply=false", "--expect-digest", digest},
		{"rollback", "--cohort", "cdom", "--apply", "--expect-digest", digest},
		{"activate", "--cohort", "cdom", "--apply", "--expect-digest", strings.ToUpper(digest)},
		{"activate", "--cohort", "cdom", "--apply", "--expect-digest", digest, "extra"},
	} {
		if _, err := parseB0PlanOptions(args); err == nil {
			t.Fatal("unbound administrative invocation accepted", args)
		}
	}
}
