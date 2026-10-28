package queue

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
)

func TestRealFirstRetirementHistoryRefusesUnownedBoardFences(t *testing.T) {
	for _, kind := range []string{"monitor", "scrape"} {
		t.Run(kind, func(t *testing.T) {
			old := firstOwnershipFixture(t)
			firstRetirementClaim(t, old)
			if _, err := applyFirstFixture(t, old, true); err != nil {
				t.Fatal(err)
			}
			// A retained plan authorizes only its own boards. An unrelated receipt at
			// that same retired epoch must not inherit its monitor/detail coverage.
			board := old.target.document.Boards[0].ID
			task := board
			if kind == "scrape" {
				task = ordinaryID(t)
			}
			_, err := old.f.observer.Exec(context.Background(), `INSERT INTO ordinary_worker_write_fence
    (task_kind,task_id,board_id,routing_epoch,claim_token,config_sha256,state)
    VALUES($1,$2::uuid,$3::uuid,$4,$5,$6,'active')`, kind, task, board, old.f.epoch, strings.Repeat("c", 32), strings.Repeat("d", 64))
			if err != nil {
				t.Fatal(err)
			}
			next := firstOwnershipFixtureHistory(t, true)
			firstRetirementClaim(t, next)
			before, canonical := snapshot(t, next.f.client), coldCanonicalSnapshot(t, next.f)
			if _, err := applyFirstFixture(t, next, true); !errors.Is(err, ErrAuthorityLost) {
				t.Fatal("historical ownership admitted an unrelated board", err)
			}
			if !reflect.DeepEqual(before, snapshot(t, next.f.client)) || canonical != coldCanonicalSnapshot(t, next.f) || firstFixtureState(t, next) != "active" {
				t.Fatal("foreign historical fence refusal changed data or authority")
			}
		})
	}
}

func TestRealFirstOwnershipRetiredDetailHistoryDoesNotRequireCurrentPosting(t *testing.T) {
	old := firstNotionDetailHistoryFixture(t)
	_, claim := firstRetirementClaim(t, old)
	if _, err := applyFirstFixture(t, old, true); err != nil {
		t.Fatal(err)
	}
	if _, err := old.f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", claim.task.ID); err != nil {
		t.Fatal(err)
	}
	next := firstOwnershipFixtureHistory(t, true)
	if _, err := applyFirstFixture(t, next, false); err != nil {
		t.Fatal("legitimate retired detail receipt required a current posting", err)
	}
	firstRetirementClaim(t, next)
	if _, err := applyFirstFixture(t, next, true); err != nil {
		t.Fatal("retained detail history blocked supported retirement", err)
	}
}
