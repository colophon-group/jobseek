package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

var safeRegistryBoardSlug = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,79}$`)

// The transaction remains the gate. This only annotates its bounded row error
// for CI; it never prints provider URLs, metadata, or an underlying parser error.
func nativeRepositoryFailure(err error, boards []registryBoard, sourceRows ...int) error {
	const prefix = "native registry eligibility rejected row "
	text := err.Error()
	if !strings.HasPrefix(text, prefix) {
		return err
	}
	row, parseErr := strconv.Atoi(strings.TrimPrefix(text, prefix))
	if parseErr != nil || row < 1 || row > len(boards) {
		return err
	}
	board := boards[row-1]
	displayRow := row
	if len(sourceRows) == len(boards) {
		displayRow = sourceRows[row-1]
	}
	slug := "unknown"
	if board.Slug != nil && safeRegistryBoardSlug.MatchString(*board.Slug) {
		slug = *board.Slug
	}
	config := map[string]string{
		"board_slug": slug, "board_url": board.URL, "crawler_type": board.Monitor,
		"company_id": "00000000-0000-4000-8000-000000000002", "metadata": board.Metadata,
		"domain": board.Domain, "throttle_key": board.Domain,
		"check_interval_minutes": "60", "scrape_interval_hours": "24",
		"monitor_needs_browser": "0", "scraper_needs_browser": "0",
	}
	if board.MonitorBrowser {
		config["monitor_needs_browser"] = "1"
	}
	if board.ScraperBrowser {
		config["scraper_needs_browser"] = "1"
	}
	reason := "native path unsupported"
	if _, monitorErr := queue.InspectRichMonitor("00000000-0000-4000-8000-000000000001", config); monitorErr != nil {
		reason = "monitor unsupported"
		var metadata map[string]any
		if json.Unmarshal([]byte(board.Metadata), &metadata) == nil {
			metadata["scraper_type"] = "skip"
			delete(metadata, "scraper_config")
			if body, encodeErr := json.Marshal(metadata); encodeErr == nil {
				withoutDetail := make(map[string]string, len(config))
				for key, value := range config {
					withoutDetail[key] = value
				}
				withoutDetail["metadata"] = string(body)
				withoutDetail["scraper_needs_browser"] = "0"
				if queue.ValidateNativeBoard("00000000-0000-4000-8000-000000000001", withoutDetail) == nil {
					reason = "detail unsupported"
				}
			}
		}
	} else if queue.ValidateNativeBoard("00000000-0000-4000-8000-000000000001", config) != nil {
		reason = "detail unsupported"
	}
	return fmt.Errorf("native registry eligibility rejected CSV row %d board %s: %s", displayRow, slug, reason)
}

func nativeBoardRowKey(row map[string]*string) string {
	if slug := registryText(row, "board_slug"); slug != "" {
		return registryText(row, "company_slug") + "\x00" + slug
	}
	return registryText(row, "company_slug") + "\x00" + registryText(row, "board_url")
}

// The full catalogue is populated separately by TestRegistryPostgresActualRepository.
// Existing sitemap rows can depend on runtime-discovered roots retained in the
// production database, which a fresh CI database cannot invent. Gate every
// added or changed candidate row through the real canonical SQL projection.
func TestRegistryNativeChangedRepositoryBoards(t *testing.T) {
	directory, baselinePath := os.Getenv("REGISTRY_TEST_DATA_DIR"), os.Getenv("REGISTRY_TEST_BASE_BOARDS")
	if directory == "" || baselinePath == "" {
		t.Skip("repository data and base boards CSV are required")
	}
	baseBody, err := os.ReadFile(baselinePath)
	if err != nil {
		t.Fatal(err)
	}
	base, err := parseRegistryCSV(baseBody)
	if err != nil {
		t.Fatal(err)
	}
	tables, err := loadRegistryTables(directory)
	if err != nil {
		t.Fatal(err)
	}
	previous := map[string]map[string]*string{}
	for _, row := range base.Rows {
		key := nativeBoardRowKey(row)
		if _, exists := previous[key]; exists {
			t.Fatal("baseline contains duplicate board identity")
		}
		previous[key] = row
	}
	changed := registryTable{Columns: tables["boards"].Columns, Rows: []map[string]*string{}}
	positions := []int{}
	seen := map[string]bool{}
	for index, row := range tables["boards"].Rows {
		key := nativeBoardRowKey(row)
		if seen[key] {
			t.Fatalf("candidate contains duplicate board identity at CSV row %d", index+1)
		}
		seen[key] = true
		if !reflect.DeepEqual(row, previous[key]) {
			changed.Rows = append(changed.Rows, row)
			positions = append(positions, index+1)
		}
	}
	if len(changed.Rows) == 0 {
		t.Log("no added or changed board rows; full catalogue SQL projection is covered by the companion test")
		return
	}
	boards, err := prepareRegistryBoards(changed)
	if err != nil || len(boards) != len(changed.Rows) {
		t.Fatalf("changed board preparation omitted a CSV row: %v", err)
	}
	plan, err := registryTaxonomyPlan(tables)
	if err != nil {
		t.Fatal(err)
	}
	companies, err := registryCompanyPlan(tables["companies"], tables["company_descriptions"])
	if err != nil {
		t.Fatal(err)
	}
	// This transaction stages only changed boards. Public links for the full
	// catalogue are exercised by TestRegistryPostgresActualRepository.
	tables["source_coverage_links"] = registryTable{Columns: tables["source_coverage_links"].Columns}
	ctx, conn, _ := registryTestDatabase(t)
	effects, err := commitRegistryWithNativeEligibility(ctx, conn, tables, append(plan, companies...), boards, true)
	if err != nil {
		t.Fatal(nativeRepositoryFailure(err, boards, positions...))
	}
	if len(effects.Schedules) != len(changed.Rows) {
		t.Fatalf("native transaction projected %d/%d changed boards", len(effects.Schedules), len(changed.Rows))
	}
	for _, schedule := range effects.Schedules {
		if schedule.BoardID == "" || schedule.Config["company_id"] == "" {
			t.Fatal("native admission lost canonical board or company identity")
		}
	}
	t.Logf("validated %d added or changed board rows through canonical native transaction", len(changed.Rows))
}

func nativeFixtureBoards(t *testing.T, rows string) []registryBoard {
	t.Helper()
	table, err := parseRegistryCSV([]byte("company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n" + rows))
	if err != nil {
		t.Fatal(err)
	}
	boards, err := prepareRegistryBoards(table)
	if err != nil || len(boards) != len(table.Rows) {
		t.Fatalf("prepared %d/%d boards: %v", len(boards), len(table.Rows), err)
	}
	return boards
}

func nativeCSVCell(value string) string {
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

func TestRegistryNativePreparedBeisenAndDetailEligibility(t *testing.T) {
	ctx, conn, _ := registryTestDatabase(t)
	company, err := parseRegistryCSV([]byte("slug,name\nh-world-group,H World Group\n"))
	if err != nil {
		t.Fatal(err)
	}
	plan, err := registryCompanyPlan(company, registryTable{})
	if err != nil {
		t.Fatal(err)
	}
	tables := emptyReviewedSourceLinks(t)
	const beisen = `{"tenant":"huazhu1","variant":"modern","portal_id":"391d099e-32e1-4794-9556-982831a00afe","tenant_id":104091}`
	badOrigin := nativeFixtureBoards(t, "h-world-group,h-world-group-china-campus,https://campus.hworld.com/jobs,beisen,"+nativeCSVCell(beisen)+",skip,\n")
	if _, err = commitRegistryWithNativeEligibility(ctx, conn, tables, plan, badOrigin, true); err == nil {
		t.Fatal("custom-origin Beisen board passed the canonical native transaction")
	} else if got := nativeRepositoryFailure(err, badOrigin).Error(); got != "native registry eligibility rejected CSV row 1 board h-world-group-china-campus: monitor unsupported" {
		t.Fatal(got)
	}
	var count int
	if err = conn.QueryRow(ctx, "SELECT count(*) FROM job_board").Scan(&count); err != nil || count != 0 {
		t.Fatalf("rejected board committed: %d %v", count, err)
	}

	valid := nativeFixtureBoards(t, "h-world-group,h-world-group-china-social,https://huazhu1.zhiye.com/jobs,beisen,"+nativeCSVCell(beisen)+",skip,\n"+
		"h-world-group,h-world-group-control,https://job-boards.greenhouse.io/hworldcontrol,greenhouse,"+nativeCSVCell(`{"token":"hworldcontrol"}`)+",skip,\n")
	effects, err := commitRegistryWithNativeEligibility(ctx, conn, tables, plan, valid, true)
	if err != nil || len(effects.Schedules) != 2 {
		t.Fatalf("valid Beisen and skip-detail control rejected: %v", err)
	}
	beisenID := effects.Schedules[0].BoardID
	_, err = conn.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"recent_discovered_counts":[2,3],"suspect_streak":1}'::jsonb WHERE id=$1::uuid`, beisenID)
	if err != nil {
		t.Fatal(err)
	}
	effects, err = commitRegistryWithNativeEligibility(ctx, conn, tables, plan, valid[:1], true)
	if err != nil || len(effects.Schedules) != 1 || effects.Schedules[0].BoardID != beisenID {
		if err != nil {
			t.Fatalf("native resync lost canonical board identity: %v", nativeRepositoryFailure(err, valid[:1]))
		}
		t.Fatal("native resync lost canonical board identity")
	}
	var metadata map[string]any
	if err = json.Unmarshal([]byte(effects.Schedules[0].Config["metadata"]), &metadata); err != nil || metadata["recent_discovered_counts"] == nil || metadata["suspect_streak"] == nil {
		t.Fatalf("retained metadata failed native eligibility: %v", err)
	}
	if err = conn.QueryRow(ctx, "SELECT count(*) FROM job_board WHERE is_enabled").Scan(&count); err != nil || count != 1 {
		t.Fatalf("native resync failed to disable removed board: %d %v", count, err)
	}

	badDetail := nativeFixtureBoards(t, "h-world-group,h-world-group-bad-detail,https://job-boards.greenhouse.io/hworldcontrol,greenhouse,"+nativeCSVCell(`{"token":"hworldcontrol"}`)+",unsupported-fixture,\n")
	if _, err = commitRegistryWithNativeEligibility(ctx, conn, tables, plan, badDetail, true); err == nil {
		t.Fatal("unsupported detail passed canonical native transaction")
	} else if got := nativeRepositoryFailure(err, badDetail).Error(); got != "native registry eligibility rejected CSV row 1 board h-world-group-bad-detail: detail unsupported" {
		t.Fatal(got)
	}
	if err = conn.QueryRow(context.Background(), "SELECT count(*) FROM job_board WHERE is_enabled").Scan(&count); err != nil || count != 1 {
		t.Fatalf("failed detail transaction changed committed registry: %d %v", count, err)
	}
}
