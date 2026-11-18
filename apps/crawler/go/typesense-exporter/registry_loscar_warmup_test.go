package main

import (
	"encoding/json"
	"os"
	"testing"

	queue "github.com/colophon-group/jobseek/apps/crawler/go/ordinary-queue"
)

// Snapshot of the held Michel Reybier PR's exact Caterer row. This covers the
// registry normalization and both native admission paths without a database.
func TestRegistryNativeLoscarCatererWarmupSourceContract(t *testing.T) {
	body, err := os.ReadFile("testdata/loscar_caterer_board.csv")
	if err != nil {
		t.Fatal(err)
	}
	table, err := parseRegistryCSV(body)
	if err != nil {
		t.Fatal(err)
	}
	boards, err := prepareRegistryBoards(table)
	if err != nil || len(boards) != 1 {
		t.Fatal("held Caterer row lost registry normalization", err)
	}
	board := boards[0]
	if !board.MonitorBrowser || !board.ScraperBrowser {
		t.Fatal("Caterer browser lanes lost")
	}
	config := map[string]string{
		"board_slug": "michel-reybier-hospitality-loscar-caterer", "board_url": board.URL,
		"crawler_type": board.Monitor, "company_id": "00000000-0000-4000-8000-000000000002",
		"metadata": board.Metadata, "domain": board.Domain, "throttle_key": board.Domain,
		"check_interval_minutes": "60", "scrape_interval_hours": "24",
		"monitor_needs_browser": "1", "scraper_needs_browser": "1",
	}
	const boardID = "00000000-0000-4000-8000-000000000001"
	if err := queue.ValidateNativeBoard(boardID, config); err != nil {
		t.Fatal("held Caterer row failed native admission", err)
	}
	for _, path := range []string{
		"/job/chef-de-partie/l-oscar-london-job108148574",
		"/job/night-manager/l-oscar-london-job108233208",
		"/job/pastry-chef-de-partie/l-oscar-london-job108233197",
	} {
		profile, err := queue.InspectRenderedDetail(boardID, config, "https://www.caterer.com"+path, queue.Browser)
		if err != nil || profile.Profile != "dom.rendered-detail/v1" || profile.DOMConfig["warmup_url"] != board.URL {
			t.Fatalf("current Caterer detail route %s lost native warmup: %v", path, err)
		}
	}
	var metadata map[string]any
	if err := json.Unmarshal([]byte(board.Metadata), &metadata); err != nil {
		t.Fatal(err)
	}
	metadata["scraper_config"].(map[string]any)["warmup_url"] = "https://other.example/jobs"
	changed, _ := json.Marshal(metadata)
	config["metadata"] = string(changed)
	if err := queue.ValidateNativeBoard(boardID, config); err == nil {
		t.Fatal("cross-origin Caterer warmup admitted")
	}
}
