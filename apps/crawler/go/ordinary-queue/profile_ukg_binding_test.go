package queue

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
)

const ukgBindingListing = "https://recruiting.ultipro.com/ABC123/JobBoard/11111111-1111-1111-1111-111111111111"
const ukgBindingMetadata = `{"scraper_type":"embedded","scraper_config":{"variable":"job","path":"job","fields":{"title":"title","description":"description"}}}`

func TestUKGResolvedBindingSurvivesOnlyEquivalentDiscovery(t *testing.T) {
	original := profileConfig()
	original["crawler_type"], original["board_url"], original["metadata"] = "ukg", ukgBindingListing, ukgBindingMetadata
	base, err := InspectRichMonitor(profileBoardID, original)
	if err != nil {
		t.Fatal(err)
	}
	detail, err := inspectDetailOwnership(profileBoardID, original)
	if err != nil || detail.EffectiveBoardSHA256 != base.EffectiveConfigSHA256 {
		t.Fatal("detail binding", err)
	}
	for _, tc := range []struct {
		name, extra string
		equivalent  bool
	}{
		{"listing", `,"listing_url":"` + ukgBindingListing + `"`, true},
		{"identifiers", `,"host":"recruiting.ultipro.com","tenant":"ABC123","board_id":"11111111-1111-1111-1111-111111111111"`, true},
		{"all", `,"listing_url":"` + ukgBindingListing + `","host":"recruiting.ultipro.com","tenant":"ABC123","board_id":"11111111-1111-1111-1111-111111111111"`, true},
		{"case-and-space", `,"host":" RECRUITING.ULTIPRO.COM. ","tenant":" ABC123 ","board_id":" 11111111-1111-1111-1111-111111111111 "`, true},
		{"null-aliases", `,"listing_url":null,"host":null,"tenant":null,"board_id":null`, true},
		{"other-host", `,"host":"recruiting2.ultipro.com","tenant":"ABC123","board_id":"11111111-1111-1111-1111-111111111111"`, false},
		{"other-tenant", `,"listing_url":"` + strings.Replace(ukgBindingListing, "ABC123", "DEF456", 1) + `"`, false},
		{"other-board", `,"board_id":"22222222-2222-2222-2222-222222222222","host":"recruiting.ultipro.com","tenant":"ABC123"`, false},
		{"foreign-listing", `,"listing_url":"https://foreign.example.net/jobs"`, false},
		{"conflicting-host", `,"listing_url":"` + ukgBindingListing + `","host":"recruiting2.ultipro.com"`, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			changed := cloneConfig(original)
			changed["metadata"] = strings.TrimSuffix(ukgBindingMetadata, "}") + tc.extra + "}"
			m, e := InspectRichMonitor(profileBoardID, changed)
			d, f := inspectDetailOwnership(profileBoardID, changed)
			if e != nil || f != nil {
				t.Fatal("supported fixture", e, f)
			}
			if (m.EffectiveConfigSHA256 == base.EffectiveConfigSHA256) != tc.equivalent || (d.EffectiveBoardSHA256 == detail.EffectiveBoardSHA256) != tc.equivalent {
				t.Fatal("binding equality differs from resolved identity")
			}
			_, _, e = inspectMonitorConfigs(profileBoardID, original, changed)
			_, _, f = inspectDetailConfigs(profileBoardID, original, changed)
			if tc.equivalent && (e != nil || f != nil) || !tc.equivalent && (!errors.Is(e, ErrAuthorityLost) || !errors.Is(f, ErrAuthorityLost)) {
				t.Fatal("canonical/cache comparison", e, f)
			}
			var originalMD map[string]any
			if json.Unmarshal([]byte(original["metadata"]), &originalMD) != nil || len(originalMD) != 2 {
				t.Fatal("normalization mutated input")
			}
		})
	}
	changed := cloneConfig(original)
	changed["metadata"] = strings.Replace(ukgBindingMetadata, `"description":"description"`, `"description":"other"`, 1)
	m, e := InspectRichMonitor(profileBoardID, changed)
	d, f := inspectDetailOwnership(profileBoardID, changed)
	if e != nil || f != nil || m.EffectiveConfigSHA256 == base.EffectiveConfigSHA256 || d.EffectiveBoardSHA256 == detail.EffectiveBoardSHA256 {
		t.Fatal("changed parser lost fence", e, f)
	}
}
