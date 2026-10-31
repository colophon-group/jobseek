package worker

import (
	"context"
	"testing"
	"time"
)

func TestMonitorFailureContextUsesOnlyOwnedRemainingGrace(t *testing.T) {
	for _, mode := range []string{"valid", "no_guard", "parent_canceled", "foreign_deadline", "grace_elapsed", "settled", "processing_live"} {
		t.Run(mode, func(t *testing.T) {
			parent, stop := context.WithCancel(context.Background())
			defer stop()
			deadline := time.Now().Add(-time.Second)
			if mode == "processing_live" {
				deadline = time.Now().Add(time.Second)
			}
			ctx, cancel := context.WithDeadline(parent, deadline)
			defer cancel()
			guard := &claimLeaseGuard{parent: parent, processingDeadline: deadline, failureDeadline: time.Now().Add(time.Second)}
			switch mode {
			case "parent_canceled":
				stop()
			case "foreign_deadline":
				guard.processingDeadline = deadline.Add(time.Second)
			case "grace_elapsed":
				guard.failureDeadline = time.Now().Add(-time.Second)
			case "settled":
				guard.settled = true
			}
			if mode != "no_guard" {
				ctx = context.WithValue(ctx, claimLeaseGuardKey{}, guard)
			}
			cleanup, finish, ok := monitorFailureContext(ctx)
			if ok != (mode == "valid") {
				t.Fatal("failure cleanup escaped its owned deadline/grace conditions")
			}
			if !ok {
				return
			}
			defer finish()
			actual, exists := cleanup.Deadline()
			if !exists || actual != guard.failureDeadline || cleanup.Err() != nil {
				t.Fatal("cleanup extended or lost the existing absolute grace deadline")
			}
			stop()
			select {
			case <-cleanup.Done():
			case <-time.After(time.Second):
				t.Fatal("parent shutdown failed to cancel failure cleanup")
			}
		})
	}
}
