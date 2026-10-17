package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"testing"
)

func TestQualifiedDOMHTTPPaginationBindsOriginalConfigAndBoard(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"pagination":{"browser":true,"param_name":"page","max_pages":100},"url_filter":{"include":"/jobs/"},"scraper_type":"json-ld"}`
	board := sha256.Sum256([]byte(profileBoardID))
	sources := map[string]string{domHTTPPaginationConfigFingerprint(c): hex.EncodeToString(board[:])}
	if !domHTTPPaginationSourceMatches(profileBoardID, c, sources) {
		t.Fatal("original binding rejected")
	}
	if domHTTPPaginationSourceMatches("00000000-0000-0000-0000-000000000099", c, sources) {
		t.Fatal("foreign board accepted")
	}
	clone := cloneConfig(c)
	clone["metadata"] = `{"scraper_type":"json-ld","url_filter":{"include":"/jobs/"},"pagination":{"max_pages":100,"param_name":"page","browser":true},"render":true,"suspect_streak":2}`
	if !domHTTPPaginationSourceMatches(profileBoardID, clone, sources) {
		t.Fatal("JSON ordering or runtime counters changed the binding")
	}
	for key, value := range map[string]string{"board_url": "https://foreign.example/jobs", "monitor_needs_browser": "0", "scraper_needs_browser": "0", "metadata": `{"render":true,"proxy":true,"pagination":{"browser":true,"param_name":"page","max_pages":100},"url_filter":{"include":"/jobs/"},"scraper_type":"json-ld"}`} {
		clone := cloneConfig(c)
		clone[key] = value
		if domHTTPPaginationSourceMatches(profileBoardID, clone, sources) {
			t.Fatal("changed full original configuration accepted", key)
		}
	}
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || MonitorWorker(p) != Browser || !DOMMonitorUsesHeldPagination(c) || DOMMonitorHasQualifiedHTTPPagination(profileBoardID, c) {
		t.Fatal("generic pagination lost its held session or acquired qualified HTTP authority", err)
	}
}
