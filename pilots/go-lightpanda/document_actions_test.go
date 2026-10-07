package main

import (
	"context"
	"errors"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"testing"
	"time"
)

func TestDocumentActionsOptionalRequiredDeadlineAndPolicy(t *testing.T) {
	for _, mode := range []string{"optional", "required", "action-timeout", "parent-cancel", "publisher"} {
		t.Run(mode, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			n, checks := 0, 0
			pipeline := []actions.Action{{Kind: "evaluate", Script: "private-script", TimeoutMS: 1, Required: mode == "required"}, {Kind: "wait", TimeoutMS: 100}}
			err := runDocumentActions(ctx, pipeline, func(ctx context.Context, a actions.Action) error {
				n++
				if n > 1 {
					return nil
				}
				if mode == "parent-cancel" {
					cancel()
				}
				if mode == "action-timeout" {
					<-ctx.Done()
					return ctx.Err()
				}
				return errors.New("private exception")
			}, func() (bool, error) { checks++; return mode == "publisher" && checks == 2, nil })
			if mode == "required" {
				if !errors.Is(err, errDocumentAction) || n != 1 {
					t.Fatal("required partial pipeline accepted", n, err)
				}
			} else if mode == "parent-cancel" {
				if !errors.Is(err, context.Canceled) || n != 1 {
					t.Fatal("cancel swallowed", n, err)
				}
			} else {
				if err != nil || n != map[bool]int{true: 1, false: 2}[mode == "publisher"] {
					t.Fatal("optional failure or policy changed", n, err)
				}
			}
		})
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	if err := executeDocumentAction(ctx, actions.Action{Kind: "wait", Milliseconds: 1000}); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("wait ignored deadline", err)
	}
}
