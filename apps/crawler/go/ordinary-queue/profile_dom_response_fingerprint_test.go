package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestDOMResponseFingerprintBindsIndependentPDFIdentityContract(t *testing.T) {
	c := domMonitorConfig()
	c["scraper_needs_browser"] = "0"
	c["metadata"] = `{"link_selector":"a[href]","fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf","scraper_config":{"ocr":true}}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || p.Profile != "dom.direct-urls/v1" {
		t.Fatal("native mutable document monitor unavailable", err)
	}
	o, err := DOMMonitorOptions(c)
	if err != nil || o.ResponseFingerprintType != "application/pdf" {
		t.Fatal("fingerprint contract lost", err)
	}
	c["metadata"] = `{"link_selector":"a[href]","fingerprint_response":{"content_type":" Application/PDF "},"scraper_type":"pdf","scraper_config":{"ocr":true}}`
	changed, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || changed.EffectiveConfigSHA256 == p.EffectiveConfigSHA256 {
		t.Fatal("original fingerprint configuration is not bound", err)
	}
	for _, metadata := range []string{
		`{"fingerprint_response":true,"scraper_type":"pdf"}`,
		`{"fingerprint_response":{"content_type":"application/pdf","unknown":true},"scraper_type":"pdf"}`,
		`{"fingerprint_response":{"content_type":""},"scraper_type":"pdf"}`,
		`{"fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"skip"}`,
		`{"fingerprint_response":{"content_type":"text/html"},"scraper_type":"pdf"}`,
		`{"fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf","fetch_url_transform":{"find":"listing","replace":"read"}}`,
	} {
		c["metadata"] = metadata
		if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
			t.Fatal("unsupported document fingerprint contract admitted", metadata)
		}
	}
	c["monitor_needs_browser"] = "1"
	c["metadata"] = `{"render":true,"fingerprint_response":{"content_type":"application/pdf"},"scraper_type":"pdf"}`
	if _, err := InspectRichMonitor(profileBoardID, c); err == nil {
		t.Fatal("rendered route silently ignored fingerprint requests")
	}
}

func TestConfiguredMutableDocumentMonitorRetainsAllOriginalControls(t *testing.T) {
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	for _, row := range rows[1:] {
		if row[1] != "international-basketball-federation-hq" {
			continue
		}
		var metadata map[string]any
		var detail any
		if json.Unmarshal([]byte(row[4]), &metadata) != nil || json.Unmarshal([]byte(row[6]), &detail) != nil {
			t.Fatal("configured document source unavailable")
		}
		metadata["scraper_type"], metadata["scraper_config"] = row[5], detail
		body, err := json.Marshal(metadata)
		if err != nil {
			t.Fatal(err)
		}
		config := domMonitorConfig()
		config["board_slug"], config["board_url"], config["metadata"], config["scraper_needs_browser"] = row[1], row[2], string(body), "0"
		profile, err := InspectRichMonitor(profileBoardID, config)
		if err != nil || profile.Profile != "dom.direct-urls/v1" {
			t.Fatal("original configured monitor does not compile", err)
		}
		return
	}
	t.Fatal("configured mutable document board absent")
}
