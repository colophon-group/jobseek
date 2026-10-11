package queue

import (
	"encoding/csv"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	api "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func TestUnisanteEscapedMigrationFlagRetainsSemanticGuard(t *testing.T) {
	config := profileConfig()
	config["crawler_type"], config["board_slug"], config["board_url"] = "unisante", "unisante-emploi", "https://emploi.unisante.ch/index.php/offres"
	config["metadata"] = `{"scraper_type":"skip","identity_migration":"unisante-provider-reference-v1"}`
	p, err := InspectRichMonitor(profileBoardID, config)
	if err != nil {
		t.Fatal(err)
	}
	config["metadata"] = `{"scraper_type":"skip","identity_migration":"unisante\u002dprovider-reference-v1"}`
	md, err := unisanteMigrationConfig(config)
	if err != nil || !unisanteMigrationRequested(md) {
		t.Fatal("semantic migration flag escaped complete-inventory gate", err)
	}
	q, err := InspectRichMonitor(profileBoardID, config)
	if err != nil || p.EffectiveConfigSHA256 != q.EffectiveConfigSHA256 {
		t.Fatal("equivalent migration flag retired ownership", err)
	}
}

func TestLastHTTPCanonicalRegistryAndPairedDetails(t *testing.T) {
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	head := map[string]int{}
	for i, key := range rows[0] {
		head[key] = i
	}
	counts := map[string]int{}
	for _, row := range rows[1:] {
		provider := row[head["monitor_type"]]
		if !LastHTTPProvider(provider) {
			continue
		}
		t.Run(row[head["board_slug"]], func(t *testing.T) {
			md := map[string]any{}
			monitorRaw := row[head["monitor_config"]]
			if strings.TrimSpace(monitorRaw) == "" {
				monitorRaw = "{}"
			}
			if json.Unmarshal([]byte(monitorRaw), &md) != nil {
				t.Fatal("registry monitor metadata")
			}
			md["scraper_type"] = row[head["scraper_type"]]
			var detail any
			detailRaw := row[head["scraper_config"]]
			if strings.TrimSpace(detailRaw) == "" {
				detailRaw = "null"
			}
			if json.Unmarshal([]byte(detailRaw), &detail) != nil {
				t.Fatal("registry detail metadata")
			}
			md["scraper_config"] = detail
			raw, _ := json.Marshal(md)
			config := profileConfig()
			config["board_slug"], config["crawler_type"], config["board_url"], config["metadata"] = row[head["board_slug"]], provider, row[head["board_url"]], string(raw)
			p, err := InspectRichMonitor(profileBoardID, config)
			if err != nil || p.Provider != provider || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != (md["proxy"] == true) {
				t.Fatal("canonical monitor binding changed", err)
			}
			o, err := api.LastHTTPOptionsFromMetadata(provider, config["board_url"], config["metadata"])
			if err != nil || !SecondaryMonitorResourceMatches(p, config, o.ListingURL()) || SecondaryMonitorResourceMatches(p, config, "https://foreign.example/jobs") {
				t.Fatal("canonical resource scope changed", err)
			}
			if provider == "unisante" {
				assertSQLOnlySkipDetailOwnership(t, config)
				md["_identity_migration_receipt"] = map[string]any{"id": "unisante-provider-reference-v1", "version": 1, "completed_at": "2026-10-10", "updated_count": 1, "retired_count": 2}
				b, _ := json.Marshal(md)
				after := cloneConfig(config)
				after["metadata"] = string(b)
				q, err := InspectRichMonitor(profileBoardID, after)
				if err != nil || q.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 || q.SnapshotSHA256 == p.SnapshotSHA256 {
					t.Fatal("own valid adoption receipt retired immutable ownership", err)
				}
				md["_identity_migration_receipt"] = map[string]any{"id": "wrong"}
				b, _ = json.Marshal(md)
				after["metadata"] = string(b)
				if _, err := InspectRichMonitor(profileBoardID, after); err == nil {
					t.Fatal("invalid receipt admitted")
				}
				delete(md, "_identity_migration_receipt")
			} else {
				d, err := inspectDetailOwnership(profileBoardID, config)
				if err != nil || !independentDetailProfile(d.Profile) || d.Domain != "*" || d.EffectiveBoardSHA256 != p.EffectiveConfigSHA256 {
					t.Fatal("paired detail ownership changed", err)
				}
				source := "https://jobs.papajohns.com/job/42/delivery-driver/"
				if provider == "infor" {
					source = o.InforJobURL("42", "7")
				}
				if provider == "peoplesoft" {
					source = o.PeopleSoftJobURL("42")
				}
				claimed, err := inspectDetail(profileBoardID, config, source, Simple)
				if err != nil || claimed.Profile != d.Profile || claimed.EffectiveBoardSHA256 != d.EffectiveBoardSHA256 {
					t.Fatal("actual paired detail route changed", err)
				}
				if provider != "papa_johns" {
					if _, err := inspectDetail(profileBoardID, config, "https://foreign.example/jobs/42", Simple); err == nil {
						t.Fatal("foreign paired detail admitted")
					}
				}
			}
			for _, patch := range []map[string]any{{"render": true}, {"unknown": true}, {"proxy": "true"}} {
				bad := map[string]any{}
				for k, v := range md {
					bad[k] = v
				}
				for k, v := range patch {
					bad[k] = v
				}
				b, _ := json.Marshal(bad)
				after := cloneConfig(config)
				after["metadata"] = string(b)
				if _, err := InspectRichMonitor(profileBoardID, after); err == nil {
					t.Fatal("unsupported configuration admitted")
				}
			}
		})
		counts[provider]++
	}
	if !reflect.DeepEqual(counts, map[string]int{"infor": 1, "peoplesoft": 1, "papa_johns": 1, "unisante": 1}) {
		t.Fatal("canonical four-provider coverage changed", counts)
	}
}
