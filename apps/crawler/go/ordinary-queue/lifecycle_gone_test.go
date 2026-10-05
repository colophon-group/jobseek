package queue

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"
)

func TestGreenhouseGonePythonPolicyOracle(t *testing.T) {
	body, err := os.ReadFile("testdata/python_gone.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name  string
		Now   time.Time
		State struct {
			Status  string     `json:"board_status"`
			Count   int        `json:"confirmation_count"`
			First   *time.Time `json:"first_confirmed_at"`
			Last    *time.Time `json:"last_confirmed_at"`
			Success *time.Time `json:"last_success_at"`
			Gone    *time.Time `json:"gone_at"`
		}
		Result struct {
			Status   string     `json:"board_status"`
			Count    int        `json:"confirmation_count"`
			Required int        `json:"required_confirmations"`
			Advanced bool       `json:"confirmation_advanced"`
			Terminal bool       `json:"terminal_transition"`
			First    time.Time  `json:"first_confirmed_at"`
			Last     time.Time  `json:"last_confirmed_at"`
			Gone     *time.Time `json:"gone_at"`
			Due      time.Time  `json:"next_check_at"`
		}
	}
	if err := json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) != 20 {
		t.Fatal("missing captured provider-gone policy cases")
	}
	for _, item := range cases {
		t.Run(item.Name, func(t *testing.T) {
			state := item.State
			want := item.Result
			got, err := evaluateGreenhouseGone(greenhouseGoneState{state.Status, state.Count, state.First, state.Last, state.Success, state.Gone}, item.Now)
			equalGone := (got.Gone == nil && want.Gone == nil) || (got.Gone != nil && want.Gone != nil && got.Gone.Equal(*want.Gone))
			if err != nil || got.Status != want.Status || got.Count != want.Count || got.Required != want.Required || got.Advanced != want.Advanced || got.Terminal != want.Terminal || !got.First.Equal(want.First) || !got.Last.Equal(want.Last) || !got.Due.Equal(want.Due) || !equalGone {
				t.Fatalf("provider gone mismatch: %+v error=%v", got, err)
			}
		})
	}
}

func TestRealOwnedLifecycleProviderGoneSpacingAndRecovery(t *testing.T) {
	f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
	ctx := context.Background()
	observation := GreenhouseGoneObservation{Endpoint: "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true", HTTPStatus: 404}
	for i := 1; i <= 3; i++ {
		if i > 1 {
			dueLifecycle(t, f)
		}
		if i == 3 {
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET gone_last_confirmed_at=now()-interval '6 hours' WHERE id=$1::uuid", f.task.ID); err != nil {
				t.Fatal(err)
			}
		}
		c := beginLifecycle(t, a)
		result, err := c.FinishProviderGone(ctx, observation)
		if err != nil {
			t.Fatal(err)
		}
		want := "gone_pending"
		gone := 0
		if i == 3 {
			want = "gone"
			gone = 1
		}
		if result.Status != want || result.Gone != gone {
			t.Fatal("provider gone failed spacing/terminal policy")
		}
		var count, transitions, strikes int
		var first, last, due time.Time
		var enabled bool
		if err := f.observer.QueryRow(ctx, "SELECT gone_confirmation_count,gone_transition_count,consecutive_failures,gone_first_confirmed_at,gone_last_confirmed_at,next_check_at,is_enabled FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&count, &transitions, &strikes, &first, &last, &due, &enabled); err != nil {
			t.Fatal(err)
		}
		wantCount := 1
		if i == 3 {
			wantCount = 2
		}
		wantTransitions := 0
		interval := 6 * time.Hour
		if i == 3 {
			wantTransitions = 1
			interval = 24 * time.Hour
		}
		if count != wantCount || transitions != wantTransitions || strikes != 0 || !enabled || !due.Equal(last.Add(interval)) {
			t.Fatal("provider signal became generic failure or lost durable recovery deadline")
		}
		settleLifecycle(t, f, c, result)
	}
	// A repeat probe of an already-gone board keeps its original due time.
	dueLifecycle(t, f)
	c := beginLifecycle(t, a)
	result, err := c.FinishProviderGone(ctx, observation)
	if err != nil || result.Gone != 0 || result.Status != "gone" {
		t.Fatal("gone probe re-delisted rows or stranded selected board")
	}
	settleLifecycle(t, f, c, result)
	// Recovery of that same selected board needs no operator config change.
	dueLifecycle(t, f)
	c = beginLifecycle(t, a)
	posting := richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/recovered-"+ordinaryID(t), "Recovered", "<p>Recovered</p>")
	if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{posting}); err != nil {
		t.Fatal(err)
	}
	result, err = c.FinishSuccess(ctx, GreenhouseInventorySummary{Discovered: 1})
	if err != nil || result.RecoveredFrom == nil || *result.RecoveredFrom != "provider_gone" {
		t.Fatal("gone board failed native nonempty recovery")
	}
	settleLifecycle(t, f, c, result)
	var status string
	var recoveries, count int
	if err := f.observer.QueryRow(ctx, "SELECT board_status,gone_recovery_count,gone_confirmation_count FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&status, &recoveries, &count); err != nil || status != "active" || recoveries != 1 || count != 0 {
		t.Fatal("gone recovery state mismatch")
	}
}

func TestRealOwnedLifecycleGoneRejectsGenericOrPartialEvidence(t *testing.T) {
	for _, mode := range []string{"status", "endpoint", "partial"} {
		t.Run(mode, func(t *testing.T) {
			f, a := lifecycleFixture(t, `{"_monitor_config_fingerprint":"fixture"}`)
			ctx := context.Background()
			c := beginLifecycle(t, a)
			observation := GreenhouseGoneObservation{Endpoint: "https://boards-api.greenhouse.io/v1/boards/fixture/jobs?content=true", HTTPStatus: 404}
			switch mode {
			case "status":
				observation.HTTPStatus = 500
			case "endpoint":
				observation.Endpoint = "https://boards-api.greenhouse.io/v1/boards/other/jobs?content=true"
			case "partial":
				if _, err := c.WriteRichBatch(ctx, []GreenhouseRichPosting{richPosting(t, "https://job-boards.greenhouse.io/fixture/jobs/"+ordinaryID(t), "Present", "<p>Body</p>")}); err != nil {
					t.Fatal(err)
				}
			}
			if result, err := c.FinishProviderGone(ctx, observation); !errors.Is(err, ErrConfiguration) || result != nil {
				t.Fatal("unproven provider disappearance acquired terminal receipt")
			}
			var status string
			if err := f.observer.QueryRow(ctx, "SELECT board_status FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&status); err != nil || status != "active" {
				t.Fatal("rejected provider signal changed lifecycle")
			}
		})
	}
}
