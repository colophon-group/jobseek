package queue

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func TestSuccessFactorsRMKCompiledSourceAndTransport(t *testing.T) {
	for _, proxy := range []bool{false, true} {
		for _, scraper := range []string{"skip", "json-ld"} {
			t.Run(fmt.Sprintf("proxy=%t/%s", proxy, scraper), func(t *testing.T) {
				md := map[string]any{"preset": "successfactors", "variant": "rmk", "brand": "Fixture", "locale": "en_US", "scraper_type": scraper, "proxy": proxy}
				raw, _ := json.Marshal(md)
				c := profileConfig()
				c["crawler_type"] = "rss"
				c["board_url"] = "https://example.com/Fixture/jobs"
				c["metadata"] = string(raw)
				p, e := InspectRichMonitor("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", c)
				if e != nil || !RSSRMKProfile(p.Profile) || MonitorWorker(p) != Simple || ProfileRequiresProxy(p.Profile) != proxy || p.Endpoint != c["board_url"] || p.SnapshotSHA256 != configDigest(c) {
					t.Fatal("RMK source or transport authority changed", e)
				}
				api := "https://example.com/services/recruiting/v1/jobs"
				if !RSSMonitorResourceMatches(p, c, api) || !initialMonitorResourceMatches(p, api) || RSSMonitorResourceMatches(p, c, api+"?extra=1") || initialMonitorResourceMatches(p, "https://other.example/services/recruiting/v1/jobs") {
					t.Fatal("RMK resource authority changed")
				}
				c["monitor_needs_browser"] = "1"
				if _, e := InspectRichMonitor("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", c); e == nil {
					t.Fatal("unqualified browser route admitted")
				}
			})
		}
	}
}
func TestSuccessFactorsRMKCanonicalThreeConfigurations(t *testing.T) {
	dir := os.Getenv("JOBSEEK_RMK_PUBLIC_CAPTURE_DIR")
	if dir == "" {
		t.Skip("requires canonical private original public captures")
	}
	for _, slug := range []string{"zhengda-group-careers-my-cpf", "zhengda-group-careers-vn-cpf", "zhengda-group-careers-th-cpf"} {
		t.Run(slug, func(t *testing.T) {
			raw, e := os.ReadFile(filepath.Join(dir, "native1005-rss-group-"+slug+"-original-public-capture1-2026-10-10.json"))
			if e != nil {
				t.Fatal(e)
			}
			var v struct{ Board map[string]json.RawMessage }
			if json.Unmarshal(raw, &v) != nil {
				t.Fatal("canonical original unavailable")
			}
			c := map[string]string{}
			for k, x := range v.Board {
				if k == "metadata" {
					c[k] = string(x)
					continue
				}
				var s string
				if json.Unmarshal(x, &s) == nil {
					c[k] = s
				}
			}
			p, e := InspectRichMonitor("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", c)
			if e != nil || !RSSRMKProfile(p.Profile) || !ProfileRequiresProxy(p.Profile) {
				t.Fatal("canonical RMK configuration rejected", e)
			}
		})
	}
}
