package queue

import (
	"encoding/json"
	"testing"
)

func TestOrderedBrowserDetailKeepsOriginalLaneAndWholeChain(t *testing.T) {
	for _, domPrimary := range []bool{false, true} {
		name := "http-jsonld-rendered-dom"
		metadata := `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"steps":[{"tag":"p","field":"description","html":true}]}}}}`
		if domPrimary {
			name = "http-dom-rendered-jsonld"
			metadata = `{"scraper_type":"dom","scraper_config":{"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld","config":{"render":true}}}}`
		}
		t.Run(name, func(t *testing.T) {
			config := jsonldDetailConfig()
			config["scraper_needs_browser"], config["metadata"] = "1", metadata
			const source = "https://jobs.example.net/job/42"
			chain, err := InspectOrderedDetail(jsonldBoardID, config, source, Browser)
			if err != nil || len(chain.Stages) != 2 {
				t.Fatal("mixed chain refused", err)
			}
			want := []string{jsonldDetailProfile, domRenderedDetailProfile}
			if domPrimary {
				want[0], want[1] = domDetailProfile, jsonldRenderedDetailProfile
			}
			for i, stage := range chain.Stages {
				if stage.Profile != want[i] || stage.EffectiveBoardSHA256 != chain.EffectiveBoardSHA256 || stage.SourceURL != source || stage.Domain != "jobs.example.net" {
					t.Fatal("stage lost parser, source or whole-chain identity")
				}
			}
			owned, err := inspectDetailOwnership(jsonldBoardID, config)
			if err != nil || owned.Profile != orderedBrowserDetailProfile || detailWorker(owned.Profile) != Browser || owned.Domain != "*" || owned.EffectiveBoardSHA256 != chain.EffectiveBoardSHA256 {
				t.Fatal("original browser ownership lost", err)
			}
			if _, err := inspectDetail(jsonldBoardID, config, source, Simple); err == nil {
				t.Fatal("mixed chain claimed from simple queue")
			}
			doc := testOwnershipDocument(t)
			md, _ := profileMetadataFields(config["metadata"], nil)
			stable, err := stableJSONLDConfig(config, md)
			if err != nil {
				t.Fatal(err)
			}
			doc.Details = []ownershipDetail{{BoardID: jsonldBoardID, Domain: "*", Profile: owned.Profile, Worker: Browser, CompanyID: owned.CompanyID, EffectiveConfigHash: owned.EffectiveBoardSHA256, Config: stable}}
			body, hash := testOwnershipBody(t, doc)
			if _, err := decodeOwnership(body, hash); err != nil {
				t.Fatal("mixed browser ownership document rejected", err)
			}
			var changed map[string]any
			json.Unmarshal([]byte(metadata), &changed)
			changed["additional_canonical_field"] = "changed"
			encoded, _ := json.Marshal(changed)
			config["metadata"] = string(encoded)
			other, err := InspectOrderedDetail(jsonldBoardID, config, source, Browser)
			if err != nil || other.EffectiveBoardSHA256 == chain.EffectiveBoardSHA256 {
				t.Fatal("whole original metadata no longer binds ownership", err)
			}
		})
	}
}

func TestOrderedBrowserRejectsUnsupportedControlsAndLaneChanges(t *testing.T) {
	for _, metadata := range []string{
		`{"scraper_type":"dom","scraper_config":{"render":true,"steps":[{"tag":"h1","field":"title"}],"fallback":{"type":"json-ld"}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"stealth":true}}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"proxy":true}}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"persistent_context":true}}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":"true"}}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom"}}}`,
		`{"scraper_type":"json-ld","scraper_config":{"enrich":["description"],"fallback":{"type":"dom","config":{"render":true}}}}`,
	} {
		config := jsonldDetailConfig()
		config["scraper_needs_browser"], config["metadata"] = "1", metadata
		if _, err := InspectOrderedDetail(jsonldBoardID, config, "https://jobs.example.net/42", Browser); err == nil {
			t.Fatal("unsupported mixed chain admitted")
		}
	}
}

func TestOrderedBrowserEmptyDOMStageRetainsQueueWithoutOriginTransport(t *testing.T) {
	config := jsonldDetailConfig()
	config["scraper_needs_browser"] = "1"
	config["metadata"] = `{"scraper_type":"json-ld","scraper_config":{"fallback":{"type":"dom","config":{"render":true,"wait":"load"}}}}`
	chain, err := InspectOrderedDetail(jsonldBoardID, config, "https://jobs.example.net/42", Browser)
	if err != nil || len(chain.Stages) != 2 || chain.Stages[0].Profile != jsonldDetailProfile || chain.Stages[1].Profile != orderedEmptyDOMDetailProfile || chain.Stages[1].DOMConfig != nil || chain.Stages[1].SkipSSL || orderedDetailOwnership(chain).Profile != orderedBrowserDetailProfile {
		t.Fatal("original no-steps fallback lost its no-fetch behavior", err)
	}
}
