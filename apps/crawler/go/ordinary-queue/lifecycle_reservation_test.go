package queue

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"
)

func TestRealOwnedLifecycleReservationIsMonotonicAndVisibilityPreserving(t *testing.T) {
	for _, mode := range []string{"pre_existing", "new_header", "during_prepare"} {
		t.Run(mode, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
			ctx := context.Background()
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET consecutive_failures=3,last_success_at=now()-interval '1 day',last_error='existing failure' WHERE id=$1::uuid", f.task.ID); err != nil {
				t.Fatal(err)
			}
			var beforeSuccess time.Time
			var beforePosting string
			if err := f.observer.QueryRow(ctx, "SELECT last_success_at FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&beforeSuccess); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT titles[1] FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&beforePosting); err != nil {
				t.Fatal(err)
			}
			claim, err := a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("reservation claim unavailable")
			}
			var c *GreenhouseCycle
			if mode != "pre_existing" {
				c, err = a.BeginGreenhouseCycle(ctx, claim)
				if err != nil {
					t.Fatal(err)
				}
			}
			var observation *GreenhouseHeaderReservation
			if mode == "pre_existing" || mode == "during_prepare" {
				if _, err := f.observer.Exec(ctx, `UPDATE job_board SET tdm_reserved=true,tdm_reservation='{"source":"header","policy_url":"retained"}' WHERE id=$1::uuid`, f.task.ID); err != nil {
					t.Fatal(err)
				}
				if mode == "pre_existing" {
					if _, err := a.BeginGreenhouseCycle(ctx, claim); !errors.Is(err, ErrPublisherReserved) {
						t.Fatal("reserved board admitted fetch cycle")
					}
				}
				if mode == "during_prepare" {
					if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/blocked-"+ordinaryID(t), "Blocked", "<p>Blocked</p>")}); !errors.Is(err, ErrPublisherReserved) {
						t.Fatal("new reservation did not stop prepared content")
					}
				}
			} else {
				policy := "https://publisher.invalid/policy"
				observation = &GreenhouseHeaderReservation{Endpoint: "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true", PolicyURL: &policy}
			}
			var result *GreenhouseCycleResult
			if c == nil {
				result, err = a.FinishGreenhouseReservation(ctx, claim, observation)
			} else {
				result, err = c.FinishReservation(ctx, observation)
			}
			if err != nil || result.Status != "publisher_reserved" || result.Receipt == nil {
				t.Fatalf("reservation terminal handling failed: %v", err)
			}
			var reserved, active, postingReserved bool
			var strikes int
			var success time.Time
			var evidence []byte
			var title, message string
			if err := f.observer.QueryRow(ctx, "SELECT tdm_reserved,consecutive_failures,last_success_at,last_error,tdm_reservation FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&reserved, &strikes, &success, &message, &evidence); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT tdm_reserved,is_active,titles[1] FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&postingReserved, &active, &title); err != nil {
				t.Fatal(err)
			}
			if !reserved || !postingReserved || !active || title != beforePosting || strikes != 3 || !success.Equal(beforeSuccess) || message != "existing failure" {
				t.Fatal("reservation changed listing visibility or failure/success accounting")
			}
			var parsed map[string]any
			if err := json.Unmarshal(evidence, &parsed); err != nil {
				t.Fatal(err)
			}
			if mode == "new_header" {
				if parsed["url"] != observation.Endpoint || parsed["source"] != "header" || parsed["policy_url"] != *observation.PolicyURL || parsed["observed_at"] == nil {
					t.Fatal("new publisher evidence lost provenance")
				}
			} else if parsed["policy_url"] != "retained" {
				t.Fatal("skip rewrote prior publisher evidence")
			}
			if c == nil {
				c = &GreenhouseCycle{authority: a, claim: claim}
			}
			settleLifecycle(t, f, c, result)
			// A later scheduled skip requires no network request and preserves the flag.
			dueLifecycle(t, f)
			claim, err = a.Claim(ctx, Simple)
			if err != nil || claim == nil {
				t.Fatal("reserved owner stranded")
			}
			result, err = a.FinishGreenhouseReservation(ctx, claim, nil)
			if err != nil {
				t.Fatal(err)
			}
			settleLifecycle(t, f, &GreenhouseCycle{authority: a, claim: claim}, result)
		})
	}
}

func TestRealOwnedLifecycleReservationRejectsMissingOrWrongEvidence(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	c := beginLifecycle(t, a)
	if result, err := c.FinishReservation(ctx, nil); !errors.Is(err, ErrConfiguration) || result != nil {
		t.Fatal("unreserved board admitted no-evidence skip")
	}
	if result, err := c.FinishReservation(ctx, &GreenhouseHeaderReservation{Endpoint: "https://publisher.invalid/other"}); !errors.Is(err, ErrConfiguration) || result != nil {
		t.Fatal("foreign endpoint authorized reservation")
	}
	var reserved bool
	var state string
	if err := f.observer.QueryRow(ctx, "SELECT tdm_reserved FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&reserved); err != nil || reserved {
		t.Fatal("rejected reservation changed publisher state")
	}
	if err := f.observer.QueryRow(ctx, "SELECT state FROM ordinary_worker_write_fence WHERE task_id=$1::uuid", f.task.ID).Scan(&state); err != nil || state != "active" {
		t.Fatal("unproven skip completed claim")
	}
}
