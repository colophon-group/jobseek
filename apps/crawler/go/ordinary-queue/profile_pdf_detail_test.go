package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestPDFCurrentRegistryIndependentDetailsAndEnrichment(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	count, enriched := 0, 0
	for _, row := range rows[1:] {
		if row[h["scraper_type"]] != "pdf" {
			continue
		}
		md := map[string]any{}
		if raw := row[h["monitor_config"]]; raw != "" && json.Unmarshal([]byte(raw), &md) != nil {
			t.Fatal("monitor config")
		}
		md["scraper_type"] = "pdf"
		var options any
		raw := row[h["scraper_config"]]
		if raw != "" && json.Unmarshal([]byte(raw), &options) != nil {
			t.Fatal("PDF config")
		}
		md["scraper_config"] = options
		body, _ := json.Marshal(md)
		c := profileConfig()
		c["board_url"], c["crawler_type"], c["metadata"] = row[h["board_url"]], row[h["monitor_type"]], string(body)
		p, err := inspectDetailOwnership(profileBoardID, c)
		if err != nil || p.Profile != "pdf.public-detail/v1" || p.Domain != "*" || !independentDetailProfile(p.Profile) {
			t.Fatal(row[h["board_slug"]], err, p)
		}
		actual, err := inspectDetail(profileBoardID, c, "https://documents.example.com/job.pdf", Simple)
		if err != nil || actual.Profile != p.Profile || actual.EffectiveBoardSHA256 != p.EffectiveBoardSHA256 || actual.Domain != "documents.example.com" {
			t.Fatal("actual posting binding differs", err, actual)
		}
		if len(actual.EnrichmentFields) > 0 {
			enriched++
		}
		for _, source := range []string{"file:///job.pdf", "https://user:secret@documents.example.com/job.pdf", "https://documents.example.com:444/job.pdf"} {
			if _, err := InspectPDFDetail(profileBoardID, c, source, Simple); err == nil {
				t.Fatal("invalid source admitted", source)
			}
		}
		if _, err := InspectPDFDetail(profileBoardID, c, "https://documents.example.com/job.pdf", Browser); err == nil {
			t.Fatal("browser downgrade admitted")
		}
		count++
	}
	if count != 50 || enriched != 8 {
		t.Fatal(count, enriched)
	}
}
