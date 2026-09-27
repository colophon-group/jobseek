package main

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestBoardSyncRedisEffectsMatchPython(t *testing.T) {
	var cases []struct {
		Name  string               `json:"name"`
		Input boardSyncInput       `json:"input"`
		Seed  map[string]reaperKey `json:"seed"`
		Steps []struct {
			Failed bool                 `json:"failed"`
			State  map[string]reaperKey `json:"state"`
		} `json:"steps"`
	}
	body, err := os.ReadFile("testdata/board_sync_fixture.json")
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(body, &cases); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	client := reaperRedisFixture(t, ctx)
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			if err := seedReaperKeys(ctx, client, c.Seed); err != nil {
				t.Fatal(err)
			}
			for _, step := range c.Steps {
				err := applyBoardSync(ctx, client, c.Input, deadletterDelays{Default: 2, ATS: .5}, func() time.Time { return time.Unix(1000, 0) })
				if (err != nil) != step.Failed {
					t.Fatalf("error=%v want_failed=%v", err, step.Failed)
				}
				state, err := snapshotReaperKeys(ctx, client)
				if err != nil {
					t.Fatal(err)
				}
				if !reflect.DeepEqual(state, step.State) {
					t.Fatalf("state mismatch\nactual=%v\nexpected=%v", state, step.State)
				}
			}
		})
	}
}

func TestBoardSyncInputAndRemovalContract(t *testing.T) {
	for _, body := range []string{"null", "{} {}", `{"orphans":[["d","b","extra"]]}`, `{"orphans":[["d"]]}`, `{"schedules":[{"domain":"d","board_id":"b","next_check_at":1e999}]}`, `{"schedules":[{"domain":"","board_id":"b"}]}`, `{"unknown":true}`} {
		if _, err := decodeBoardSync(strings.NewReader(body)); err == nil {
			t.Fatalf("accepted %s", body)
		}
	}
	body, err := os.ReadFile("../../src/lua/remove_monitor.lua")
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != boardSyncRemoveLua {
		t.Fatal("queue removal Lua drift")
	}
}

func TestBoardSyncCrossesBatchBoundaryAndStopsOnError(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	client := reaperRedisFixture(t, ctx)
	input := boardSyncInput{}
	for i := 0; i < 1001; i++ {
		input.Schedules = append(input.Schedules, boardSyncSchedule{Domain: "example.test", BoardID: strings.Repeat("a", i+1), NextCheckAt: 1100, Config: map[string]string{"field": "value"}})
	}
	ticks := 0
	err := applyBoardSync(ctx, client, input, deadletterDelays{Default: 2}, func() time.Time { ticks++; return time.Unix(1000, 0) })
	if err != nil || ticks != 2 || client.ZCard(ctx, "monitors_simple:example.test").Val() != 1001 {
		t.Fatalf("batch: ticks=%d err=%v", ticks, err)
	}
	input.Orphans = [][]string{{"example.test", "a"}}
	if err := client.Set(ctx, "lightpanda-b0:legacy-guard", "corrupt", 0).Err(); err != nil {
		t.Fatal(err)
	}
	ticks = 0
	err = applyBoardSync(ctx, client, input, deadletterDelays{Default: 2}, func() time.Time { ticks++; return time.Unix(1000, 0) })
	if err == nil || ticks != 1 || client.Exists(ctx, "board:a").Val() != 1 {
		t.Fatal("error did not stop next batch/removal")
	}
}
