package queue

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"strconv"
)

const orderedHTTPDetailProfile = "detail.ordered-http/v1"
const orderedProxyHTTPDetailProfile = "detail.ordered-proxy-http/v1"
const orderedBrowserDetailProfile = "detail.ordered-browser/v1"
const orderedEmptyDOMDetailProfile = "dom.empty-ordered-detail/v1"

func isOrderedDetailProfile(profile string) bool {
	return profile == orderedHTTPDetailProfile || profile == orderedProxyHTTPDetailProfile || profile == orderedBrowserDetailProfile
}

// OrderedDetailProfile is detached evidence for the retained static scraper
// chain. It grants no pop/write authority. The attempt must bind its cached
// step, canonical source and whole original board before selecting a stage.
type OrderedDetailProfile struct {
	BoardID, CompanyID, EffectiveBoardSHA256 string
	Stages                                   []WorkdayDetailProfile
	worker                                   WorkerType
}

func orderedDetailOwnership(p OrderedDetailProfile) WorkdayDetailProfile {
	r := p.Stages[0]
	r.Profile, r.Domain = orderedHTTPDetailProfile, "*"
	for _, stage := range p.Stages {
		if ProfileRequiresProxy(stage.Profile) {
			r.Profile = orderedProxyHTTPDetailProfile
		}
	}
	if p.worker == Browser {
		r.Profile = orderedBrowserDetailProfile
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

// InspectOrderedDetail qualifies each configured parser and transport separately
// while retaining the full chain and original queue lane in ownership. Legacy single-item
// execution does not apply fallback.fields as a mask; retain that behavior.
func InspectOrderedDetail(boardID string, config map[string]string, source string, worker WorkerType) (OrderedDetailProfile, error) {
	fail := func() (OrderedDetailProfile, error) { return OrderedDetailProfile{}, ErrUnsupportedProfile }
	if (worker != Simple || config["scraper_needs_browser"] != "0") && (worker != Browser || config["scraper_needs_browser"] != "1") {
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
	p := OrderedDetailProfile{BoardID: boardID, CompanyID: config["company_id"], EffectiveBoardSHA256: hex.EncodeToString(digest[:]), worker: worker}
	hasBrowser := false
	for step := 0; step < 8; step++ {
		if kind != "dom" && kind != "json-ld" || options["enrich"] != nil {
			return fail()
		}
		next := options["fallback"]
		delete(options, "fallback")
		// The original DOM scraper returns an empty JobContent before any origin
		// request when it has neither steps nor a preset. Validate the remaining
		// stage controls with a temporary parser, then mark this as a no-fetch stage.
		// The original whole-chain configuration remains the ownership fingerprint.
		emptyDOM := kind == "dom" && (options["preset"] == nil || string(options["preset"]) == "null") && (options["steps"] == nil || string(options["steps"]) == "null" || string(options["steps"]) == "[]")
		if emptyDOM {
			options["steps"] = json.RawMessage(`[{"tag":"h1","field":"title"}]`)
		}
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
		var rendered bool
		if raw := options["render"]; worker == Browser && raw != nil && string(raw) != "null" {
			if json.Unmarshal(raw, &rendered) != nil {
				return fail()
			}
		}
		// Later legacy attempts select their own lane. Retain chains whose
		// follow-ups stay on the canonical browser lane; a return to HTTP
		// needs a separate cross-lane handoff contract.
		if worker == Browser && step > 0 && !rendered {
			return fail()
		}
		if rendered {
			if worker != Browser {
				return fail()
			}
			stageConfig["scraper_needs_browser"] = "1"
			stage, err = InspectRenderedDetail(boardID, stageConfig, source, Browser)
			hasBrowser = true
		} else {
			// This is the transport of one stage, not a change to the canonical lane.
			// The complete original board, including the rendered fallback, is hashed above.
			stageConfig["scraper_needs_browser"] = "0"
			if kind == "dom" {
				stage, err = InspectDOMDetail(boardID, stageConfig, source, Simple)
			} else {
				stage, err = InspectJSONLDDetail(boardID, stageConfig, source, Simple)
			}
		}
		if err != nil {
			return fail()
		}
		if emptyDOM {
			stage.Profile = orderedEmptyDOMDetailProfile
			stage.DOMConfig = nil
			stage.SkipSSL = false // No origin read or TLS exception is used.
		}
		stage.EffectiveBoardSHA256 = p.EffectiveBoardSHA256
		p.Stages = append(p.Stages, stage)
		if next == nil || string(next) == "null" || string(next) == "{}" {
			if len(p.Stages) < 2 || (worker == Browser && !hasBrowser) {
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
