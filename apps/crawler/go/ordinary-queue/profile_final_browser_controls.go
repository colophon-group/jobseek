package queue

import (
	"crypto/sha256"
	_ "embed"
	"encoding/hex"
	"encoding/json"
	"reflect"
)

// These two complete inventories were read with the pinned engine, including
// the original fragment navigation and repeat actions. Unknown fragments and
// changed parser/action/detail contracts retain their previous owner.
var finalFragmentSources = map[string]string{
	"ltimindtree-careers-india-europe": "a996723eead4af9eaf652649913ad61b8c5778f48105fed9583e263409b0b99e",
	"ltimindtree-careers-us-canada":    "793bd3f413fdd9af253b25dfa89824e80e333ec35e34e0c0d3ef936411fdc568",
}

func finalFragmentFingerprint(config map[string]string, md map[string]json.RawMessage) string {
	copyMD := make(map[string]json.RawMessage, len(md))
	for key, value := range md {
		if key != "_monitor_config_fingerprint" {
			copyMD[key] = value
		}
	}
	stable, err := stableJSONLDConfig(config, copyMD)
	if err != nil {
		return ""
	}
	body, err := json.Marshal([]string{config["board_url"], config["monitor_needs_browser"], config["scraper_needs_browser"], stable["metadata"]})
	if err != nil {
		return ""
	}
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:])
}

func qualifiedFinalFragmentMonitor(config map[string]string, md map[string]json.RawMessage) bool {
	expected, ok := finalFragmentSources[config["board_slug"]]
	return ok && config["crawler_type"] == "dom" && config["monitor_needs_browser"] == "1" && expected == finalFragmentFingerprint(config, md)
}

//go:embed final_browser_detail_transitions.json
var finalDetailTransitionsJSON []byte

type finalDetailTransition struct {
	Slug    string          `json:"slug"`
	URL     string          `json:"board_url"`
	Forward json.RawMessage `json:"forward_config"`
	Reverse json.RawMessage `json:"reverse_config"`
}

var finalDetailTransitions = func() []finalDetailTransition {
	var entries []finalDetailTransition
	if json.Unmarshal(finalDetailTransitionsJSON, &entries) != nil || len(entries) != 2 {
		panic("invalid committed detail transition contracts")
	}
	return entries
}()

// Changing the detail recipe must not retire a previously qualified monitor.
// Authenticate the exact detail transition and prove its unchanged monitor
// against the existing contract. Ownership still hashes the current metadata.
func qualifiedFinalDetailTransition(config map[string]string, md map[string]json.RawMessage) bool {
	if config["crawler_type"] != "dom" || config["monitor_needs_browser"] != "1" || config["scraper_needs_browser"] != "1" {
		return false
	}
	for _, entry := range finalDetailTransitions {
		if config["board_slug"] != entry.Slug || config["board_url"] != entry.URL || string(md["scraper_type"]) != `"dom"` {
			continue
		}
		var actual, expected any
		if json.Unmarshal(md["scraper_config"], &actual) != nil || json.Unmarshal(entry.Forward, &expected) != nil || !reflect.DeepEqual(actual, expected) {
			return false
		}
		legacyMD := make(map[string]json.RawMessage, len(md))
		for key, value := range md {
			legacyMD[key] = value
		}
		legacyMD["scraper_config"] = entry.Reverse
		body, err := json.Marshal(legacyMD)
		if err != nil {
			return false
		}
		legacy := cloneConfig(config)
		legacy["metadata"] = string(body)
		return selfIdentifyingDOMSource(legacy, legacyMD)
	}
	return false
}
