package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"testing"
)

func TestSharedListingContractsCurrentRegistryBinding(t *testing.T) {
	expected := map[string]bool{"alten-poland": true, "arthur-d-little-global-careers": true, "cambridge-aerospace-careers-api": true, "carrefour-france-careers-fr-franchise": true, "coop-careers-service7000": true, "cushman-wakefield-brazil": true, "cushman-wakefield-chile": true, "cushman-wakefield-mexico": true, "cushman-wakefield-peru": true, "fidelity-investments-careers-temporary": true, "maruti-suzuki-careers": true, "qnb-group-turkey": true, "vista-global-global": true}
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
			if json.Unmarshal([]byte(row[h["monitor_config"]]), &md) != nil {
				t.Fatal("metadata")
			}
			md["scraper_type"] = row[h["scraper_type"]]
			if raw := row[h["scraper_config"]]; raw != "" {
				var v any
				if json.Unmarshal([]byte(raw), &v) != nil {
					t.Fatal("scraper metadata")
				}
				md["scraper_config"] = v
			}
			raw, _ := json.Marshal(md)
			c := profileConfig()
			c["crawler_type"], c["board_url"], c["metadata"] = row[h["monitor_type"]], row[h["board_url"]], string(raw)
			if md["render"] == true || md["browser"] == true {
				c["monitor_needs_browser"] = "1"
			}
			p, e := InspectRichMonitor(profileBoardID, c)
			if e != nil {
				t.Fatal("original config rejected", e)
			}
			if (MonitorWorker(p) == Browser) != (c["monitor_needs_browser"] == "1") || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) || p.SnapshotSHA256 != configDigest(c) {
				t.Fatal("transport/binding changed")
			}
			if c["crawler_type"] == "dom" {
				o, e := DOMMonitorOptions(c)
				if e != nil || o.Pagination == nil || o.Pagination.Start < -1 || o.Pagination.Start > 1 {
					t.Fatal("listing options changed", e)
				}
				tail := o.Pagination.URL(p.Endpoint, 2)
				if !DOMMonitorResourceMatches(p, c, tail) || DOMMonitorPrimaryResourceMatches(p, c, tail) {
					t.Fatal("tail resource authority lost")
				}
			} else {
				o, e := APISnifferMonitorOptions(c)
				if e != nil || len(o.Fields) == 0 || o.HTML || o.AutoFields {
					t.Fatal("explicit fields changed", e)
				}
				if slug != "coop-careers-service7000" && !o.AutoURLField {
					t.Fatal("implicit URL identity not bound")
				}
			}
		})
		delete(expected, slug)
	}
	if len(expected) != 0 {
		t.Fatal("missing registry boards", expected)
	}
}

func TestSharedListingContractsPreserveUnsupportedBoundaries(t *testing.T) {
	for _, extra := range []string{
		`"pagination":{"param_name":"page","start":-2}`,
		`"pagination":{"param_name":"page","browser":true}`,
		`"pagination":{"param_name":"page"},"render":true,"advertised_total":{"selector":".total","regex":"([0-9]+)"}`,
		`"pagination":{"param_name":"page"},"empty_states":[{"selector":".empty","exact_text":"No jobs"}]`,
	} {
		c := profileConfig()
		c["crawler_type"] = "dom"
		c["metadata"] = `{"scraper_type":"json-ld",` + extra + `}`
		if _, e := InspectRichMonitor(profileBoardID, c); e == nil {
			t.Fatal("unsupported listing contract admitted", extra)
		}
	}
}
