package queue

import (
	"context"
	"fmt"
	"testing"
	"time"
)

func TestRealNativeClaimPreservesOriginalMonitorAttemptTimestamp(t *testing.T) {
	for _, kind := range []Kind{Monitor, Scrape} {
		for _, worker := range []WorkerType{Simple, Browser} {
			t.Run(fmt.Sprintf("%s/%s", kind, worker), func(t *testing.T) {
				f := realAuthority(t, kind, worker)
				ctx := context.Background()
				var before, originalDue time.Time
				if err := f.observer.QueryRow(ctx, "SELECT clock_timestamp(),next_check_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&before, &originalDue); err != nil {
					t.Fatal(err)
				}
				claim, err := f.authority.Claim(ctx, worker)
				if err != nil || claim == nil || claim.RecoveredReceipt() != nil {
					t.Fatal("fresh claim unavailable", err)
				}
				var checked, leased *time.Time
				var owner *string
				var after, due time.Time
				if err := f.observer.QueryRow(ctx, "SELECT last_checked_at,next_check_at,lease_owner,leased_until,clock_timestamp() FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&checked, &due, &owner, &leased, &after); err != nil {
					t.Fatal(err)
				}
				if kind == Monitor && (checked == nil || checked.Before(before) || checked.After(after)) {
					t.Fatal("accepted monitor attempt did not record the original claim timestamp")
				}
				if kind == Scrape && checked != nil {
					t.Fatal("detail claim changed monitor freshness")
				}
				if !due.Equal(originalDue) || owner != nil || leased != nil {
					t.Fatal("claim timestamp changed fenced scheduling or acquired a legacy lease")
				}
			})
		}
	}
}
