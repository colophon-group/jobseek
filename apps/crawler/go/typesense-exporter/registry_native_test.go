package main

import (
	"context"
	"strings"
	"testing"
)

func TestRegistryNativeRejectsBeforeCommitAndPublication(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	table, err := parseRegistryCSV([]byte("slug,name\nnative-fixture,Native fixture\n"))
	if err != nil {
		t.Fatal(err)
	}
	plan, err := registryCompanyPlan(table, registryTable{})
	if err != nil {
		t.Fatal(err)
	}
	board := registryBoard{Company: "native-fixture", URL: "https://job-boards.greenhouse.io/native-fixture", Monitor: "greenhouse", Metadata: `{"token":"native-fixture","scraper_type":"skip"}`, Domain: "greenhouse"}
	effects, err := commitRegistryWithNativeEligibility(ctx, conn, map[string]registryTable{}, plan, []registryBoard{board}, true)
	if err != nil || len(effects.Schedules) != 1 {
		t.Fatal("supported native registry transaction failed", err)
	}
	var originalID string
	if err := conn.QueryRow(ctx, "SELECT id::text FROM job_board WHERE board_url=$1", board.URL).Scan(&originalID); err != nil || effects.Schedules[0].BoardID != originalID {
		t.Fatal("native validation substituted for canonical identity")
	}
	bad := board
	bad.URL = "https://unsupported-native-fixture.invalid/jobs"
	bad.Monitor = "unsupported-native-fixture"
	effects, err = commitRegistryWithNativeEligibility(ctx, conn, map[string]registryTable{}, nil, []registryBoard{bad}, true)
	if err == nil || len(effects.Schedules) != 0 || len(effects.Orphans) != 0 {
		t.Fatal("unsupported config authorized downstream publication")
	}
	var invalid int
	var retained bool
	if err := conn.QueryRow(context.Background(), "SELECT count(*) FROM job_board WHERE board_url=$1", bad.URL).Scan(&invalid); err != nil || invalid != 0 {
		t.Fatal("unsupported board transaction committed")
	}
	if err := conn.QueryRow(ctx, "SELECT is_enabled FROM job_board WHERE id=$1::uuid", originalID).Scan(&retained); err != nil || !retained {
		t.Fatal("failed native publication disabled the prior catalogue")
	}
}

func TestRegistryNativeValidatesBothExecutionPaths(t *testing.T) {
	board := boardSyncSchedule{BoardID: "00000000-0000-4000-8000-000000000001", Domain: "greenhouse", Config: map[string]string{"board_url": "https://job-boards.greenhouse.io/fixture", "crawler_type": "greenhouse", "company_id": "00000000-0000-4000-8000-000000000002", "metadata": `{"token":"fixture","scraper_type":"skip"}`, "throttle_key": "greenhouse", "check_interval_minutes": "60", "scrape_interval_hours": "24", "monitor_needs_browser": "0", "scraper_needs_browser": "0"}}
	if err := validateNativeRegistrySchedules([]boardSyncSchedule{board}); err != nil {
		t.Fatal("native registry rejected supported monitor and detail", err)
	}
	for _, kind := range []string{"monitor", "detail"} {
		t.Run(kind, func(t *testing.T) {
			bad := board
			bad.Config = map[string]string{}
			for k, v := range board.Config {
				bad.Config[k] = v
			}
			if kind == "monitor" {
				bad.Config["crawler_type"] = "unknown"
			} else {
				bad.Config["metadata"] = `{"token":"fixture","scraper_type":"unsupported-fixture"}`
			}
			err := validateNativeRegistrySchedules([]boardSyncSchedule{board, bad})
			if err == nil || err.Error() != "native registry eligibility rejected row 2" || strings.Contains(err.Error(), "https:") {
				t.Fatal("native publication accepted unsupported path or exposed config")
			}
		})
	}
}
