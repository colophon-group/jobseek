package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strconv"
)

const orderedHTTPDetailProfile = "detail.ordered-http/v1"
const orderedProxyHTTPDetailProfile = "detail.ordered-proxy-http/v1"

// OrderedDetailProfile is detached evidence for the retained static scraper
// chain. It grants no pop/write authority. The attempt must bind its cached
// step, canonical source and whole original board before selecting a stage.
type OrderedDetailProfile struct {
	BoardID, CompanyID, EffectiveBoardSHA256 string
	Stages                                   []WorkdayDetailProfile
}

func orderedDetailOwnership(p OrderedDetailProfile) WorkdayDetailProfile {
	r := p.Stages[0]
	r.Profile, r.Domain = orderedHTTPDetailProfile, "*"
	for _, stage := range p.Stages {
		if ProfileRequiresProxy(stage.Profile) {
			r.Profile = orderedProxyHTTPDetailProfile
		}
	}
	return r
}

func orderedDetailStep(raw string, count int) (int, error) {
	if raw == "" {
		raw = "0"
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 || n >= count || strconv.Itoa(n) != raw {
		return 0, ErrUnsupportedProfile
	}
	return n, nil
}

// InspectOrderedDetail qualifies each configured parser separately while
// retaining the full chain in the ownership fingerprint. Legacy single-item
// execution does not apply fallback.fields as a mask; retain that behavior.
func InspectOrderedDetail(boardID string, config map[string]string, source string, worker WorkerType) (OrderedDetailProfile, error) {
	fail := func() (OrderedDetailProfile, error) { return OrderedDetailProfile{}, ErrUnsupportedProfile }
	if worker != Simple || config["scraper_needs_browser"] != "0" {
		return fail()
	}
	metadata, err := profileMetadataFields(config["metadata"], nil)
	if err != nil {
		return fail()
	}
	var kind string
	if json.Unmarshal(metadata["scraper_type"], &kind) != nil {
		return fail()
	}
	options, err := profileMetadataFields(string(metadata["scraper_config"]), nil)
	if err != nil || options["fallback"] == nil || string(options["fallback"]) == "null" {
		return fail()
	}
	stable, err := stableJSONLDConfig(config, metadata)
	if err != nil {
		return fail()
	}
	body, err := json.Marshal(struct {
		BoardID string            `json:"board_id"`
		Config  map[string]string `json:"config"`
	}{boardID, stable})
	if err != nil {
		return fail()
	}
	digest := sha256.Sum256(body)
	p := OrderedDetailProfile{BoardID: boardID, CompanyID: config["company_id"], EffectiveBoardSHA256: hex.EncodeToString(digest[:])}
	for step := 0; step < 8; step++ {
		if kind != "dom" && kind != "json-ld" || options["enrich"] != nil {
			return fail()
		}
		next := options["fallback"]
		delete(options, "fallback")
		stageMetadata := make(map[string]json.RawMessage, len(metadata))
		for key, value := range metadata {
			stageMetadata[key] = value
		}
		stageMetadata["scraper_type"], _ = json.Marshal(kind)
		stageMetadata["scraper_config"], _ = json.Marshal(options)
		stageConfig := make(map[string]string, len(config))
		for key, value := range config {
			stageConfig[key] = value
		}
		stageBody, err := json.Marshal(stageMetadata)
		if err != nil {
			return fail()
		}
		stageConfig["metadata"] = string(stageBody)
		var stage WorkdayDetailProfile
		if kind == "dom" {
			stage, err = InspectDOMDetail(boardID, stageConfig, source, worker)
		} else {
			stage, err = InspectJSONLDDetail(boardID, stageConfig, source, worker)
		}
		if err != nil {
			return fail()
		}
		stage.EffectiveBoardSHA256 = p.EffectiveBoardSHA256
		p.Stages = append(p.Stages, stage)
		if next == nil || string(next) == "null" || string(next) == "{}" {
			if len(p.Stages) < 2 {
				return fail()
			}
			return p, nil
		}
		fallback, err := profileMetadataFields(string(next), map[string]bool{"type": true, "config": true, "fields": true})
		if err != nil || json.Unmarshal(fallback["type"], &kind) != nil {
			return fail()
		}
		if raw := fallback["fields"]; raw != nil && string(raw) != "null" {
			var fields []string
			if json.Unmarshal(raw, &fields) != nil || len(fields) > 10 {
				return fail()
			}
		}
		options = map[string]json.RawMessage{}
		if raw := fallback["config"]; raw != nil && string(raw) != "null" {
			options, err = profileMetadataFields(string(raw), nil)
			if err != nil {
				return fail()
			}
		}
	}
	return fail()
}
