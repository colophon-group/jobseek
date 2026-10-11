package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func TestListingControlsBindNineCurrentRegistryConfigurations(t *testing.T) {
	expected := map[string]bool{"travel-leisure-co-rci-mexico": true, "swiss-confederation-australia": true, "international-gymnastics-federation-jobs": true, "swiss-confederation-united-states": true, "canton-of-fribourg-teaching": true, "international-skating-union-careers": true, "european-athletics-directus-careers": true, "swiss-confederation-united-kingdom": true, "international-hockey-federation-careers": true}
	f, e := os.Open("../../data/boards.csv")
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	rows, e := csv.NewReader(f).ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	h := map[string]int{}
	for i, k := range rows[0] {
		h[k] = i
	}
	for _, row := range rows[1:] {
		slug := row[h["board_slug"]]
		if !expected[slug] {
			continue
		}
		t.Run(slug, func(t *testing.T) {
			md := map[string]any{}
			d := json.NewDecoder(strings.NewReader(row[h["monitor_config"]]))
			d.UseNumber()
			if d.Decode(&md) != nil {
				t.Fatal("metadata")
			}
			md["scraper_type"] = row[h["scraper_type"]]
			if raw := row[h["scraper_config"]]; raw != "" {
				var s any
				d := json.NewDecoder(strings.NewReader(raw))
				d.UseNumber()
				if d.Decode(&s) != nil {
					t.Fatal("scraper")
				}
				md["scraper_config"] = s
			}
			raw, _ := json.Marshal(md)
			c := profileConfig()
			c["crawler_type"], c["board_url"], c["metadata"] = row[h["monitor_type"]], row[h["board_url"]], string(raw)
			if md["render"] == true {
				c["monitor_needs_browser"] = "1"
			}
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil || p.SnapshotSHA256 != configDigest(c) || (MonitorWorker(p) == Browser) != (c["monitor_needs_browser"] == "1") {
				t.Fatal("original setting/transport detached", e)
			}
			if p.Provider == "dom" {
				o, e := DOMMonitorOptions(c)
				if e != nil || (o.TitleScan == nil && o.PDFProof == nil) {
					t.Fatal("original listing proof lost", e)
				}
			} else {
				o, e := APISnifferMonitorOptions(c)
				if e != nil || o.PDFProof == nil {
					t.Fatal("original API proof lost", e)
				}
			}
		})
		delete(expected, slug)
	}
	if len(expected) > 0 {
		t.Fatal("current registry targets missing", expected)
	}
}

func TestAPIPDFProofRequiresBothControlsAndExcludesBrowserReplay(t *testing.T) {
	for _, suffix := range []string{`"require_pdf_pattern":"Employer"`, `"require_unexpired_pdf":{"pattern":"Deadline: (.+)","date_format":"%d %B %Y"}`, `"require_pdf_pattern":"Employer","require_unexpired_pdf":{"pattern":"Deadline: (.+)","date_format":"%d %B %Y"},"browser":true`} {
		c := profileConfig()
		c["crawler_type"] = "api_sniffer"
		c["metadata"] = `{"api_url":"https://example.com/api","json_path":"jobs","url_field":"url",` + suffix + `}`
		if strings.Contains(suffix, `"browser":true`) {
			c["monitor_needs_browser"] = "1"
		}
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("incomplete/unimplemented API document route admitted", suffix)
		}
	}
}
