package queue

import (
	"encoding/json"
	"testing"
)

func TestOracleADFOriginalAssignmentAndBounds(t *testing.T) {
	c := domMonitorConfig()
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"wait":"networkidle","timeout":30000,"oracle_adf_job_ids":{"max_items":100,"max_scan":1000},"url_filter":"[?]jobId=","scraper_type":"dom","scraper_config":{"render":false,"steps":[{"tag":"h1","field":"title"}]}}`
	p, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || p.Profile != domRenderedMonitorProfile || MonitorWorker(p) != Browser {
		t.Fatal("original ADF assignment rejected")
	}
	var md map[string]any
	json.Unmarshal([]byte(c["metadata"]), &md)
	for _, patch := range []map[string]any{
		{"render": false}, {"actions": []any{map[string]any{"action": "click"}}}, {"proxy": true}, {"request_headers": map[string]any{"Cookie": "private"}},
		{"oracle_adf_job_ids": map[string]any{"max_items": nil}}, {"oracle_adf_job_ids": map[string]any{"max_scan": nil}},
		{"oracle_adf_job_ids": nil}, {"oracle_adf_job_ids": map[string]any{"max_items": true}}, {"oracle_adf_job_ids": map[string]any{"max_items": 501}}, {"oracle_adf_job_ids": map[string]any{"max_items": 100, "max_scan": 99}}, {"oracle_adf_job_ids": map[string]any{"max_scan": 5001}}, {"oracle_adf_job_ids": map[string]any{"unknown": 1}},
	} {
		clone := map[string]any{}
		for k, v := range md {
			clone[k] = v
		}
		for k, v := range patch {
			clone[k] = v
		}
		b, _ := json.Marshal(clone)
		next := cloneConfig(c)
		next["metadata"] = string(b)
		if _, err := OracleADFMonitorOptions(next); err == nil {
			t.Fatal("unsupported ADF route admitted")
		}
	}
	md["oracle_adf_job_ids"] = map[string]any{"max_items": 101, "max_scan": 1000}
	b, _ := json.Marshal(md)
	c["metadata"] = string(b)
	q, e := InspectRichMonitor(profileBoardID, c)
	if e != nil || q.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("ADF bounds not immutably bound")
	}
}
