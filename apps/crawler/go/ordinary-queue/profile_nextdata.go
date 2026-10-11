package queue

import (
	"encoding/json"

	apisniffer "github.com/colophon-group/jobseek/apps/crawler/go/api-sniffer-monitor"
)

func NextdataMonitorOptions(config map[string]string) (apisniffer.NextdataOptions, error) {
	if config["crawler_type"] != "nextdata" {
		return apisniffer.NextdataOptions{}, ErrUnsupportedProfile
	}
	if config["monitor_needs_browser"] == "1" {
		o, _, err := RenderedNextdataMonitorOptions(config)
		return o, err
	}
	if config["monitor_needs_browser"] != "0" {
		return apisniffer.NextdataOptions{}, ErrUnsupportedProfile
	}
	return apisniffer.NextdataOptionsFromMetadata(config["board_url"], config["metadata"])
}

func inspectNextdataMonitor(boardID string, config map[string]string, md map[string]json.RawMessage) (GreenhouseMonitorProfile, error) {
	o, err := NextdataMonitorOptions(config)
	if err != nil {
		return GreenhouseMonitorProfile{}, ErrUnsupportedProfile
	}
	if _, err := nextdataMonitorEnrichment(config); err != nil {
		return GreenhouseMonitorProfile{}, err
	}
	profile := "nextdata.embedded-items/v1"
	if len(o.Fields) == 0 {
		profile = "nextdata.embedded-urls/v1"
	}
	if config["monitor_needs_browser"] == "1" {
		profile = "nextdata.rendered-items/v1"
		if len(o.Fields) == 0 {
			profile = "nextdata.rendered-urls/v1"
		}
	}
	return inspectURLOnlyMonitor(boardID, config, md, "nextdata", profile, "nextdata", o.BoardURL)
}

// Reuse the installed render-only navigation contract and the same document
// parser. The original configuration remains the immutable ownership binding.
func RenderedNextdataMonitorOptions(config map[string]string) (apisniffer.NextdataOptions, map[string]any, error) {
	fail := func() (apisniffer.NextdataOptions, map[string]any, error) {
		return apisniffer.NextdataOptions{}, nil, ErrUnsupportedProfile
	}
	if config["crawler_type"] != "nextdata" || config["monitor_needs_browser"] != "1" {
		return fail()
	}
	md, err := richProfileMetadata(config)
	if err != nil {
		return fail()
	}
	options := map[string]any{}
	var source string
	if raw, present := md["source"]; present && json.Unmarshal(raw, &source) != nil {
		return fail()
	}
	browserSource := source == "browser"
	yum := false
	florida := false
	if browserSource {
		var expression string
		if json.Unmarshal(md["browser_expression"], &expression) != nil {
			return fail()
		}
		florida = expression == apisniffer.FloridaCourtsBrowserExpression
		yum = expression == apisniffer.YumChinaBrowserExpression && config["board_url"] == apisniffer.YumChinaBoardURL
		if !florida && !yum {
			return fail()
		}
	}
	for _, key := range []string{"render", "browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers", "proxy", "skip_ssl", "channel", "stealth", "headless", "persistent_context", "user_agent", "resource_policy", "transport_attempts", "encoding"} {
		if raw, ok := md[key]; ok {
			var v any
			if json.Unmarshal(raw, &v) != nil {
				return fail()
			}
			options[key] = v
		}
	}
	if browserSource {
		options["render"] = true // source=browser forces rendering in Python.
	}
	// This source-bound catalog is proven on self-identifying Lightpanda.
	// Preserve the original metadata hash; do not offer generic Chromium masking.
	revolut := config["board_url"] == "https://www.revolut.com/careers/" && !browserSource && options["stealth"] == true
	if revolut {
		delete(options, "stealth")
	}
	if validateRenderedNavigation(options) != nil {
		return fail()
	}
	cloneMD := map[string]json.RawMessage{}
	for key, value := range md {
		cloneMD[key] = value
	}
	for _, key := range []string{"browser_backend", "routing_revision", "wait", "wait_fallback", "timeout", "actions", "request_headers", "resource_policy"} {
		delete(cloneMD, key)
	}
	cloneMD["render"] = json.RawMessage(`false`)
	if revolut {
		delete(cloneMD, "stealth")
	}
	if browserSource {
		cloneMD["source"] = json.RawMessage(`"nextdata"`)
		delete(cloneMD, "browser_expression")
	}
	body, err := json.Marshal(cloneMD)
	if err != nil {
		return fail()
	}
	o, err := apisniffer.NextdataOptionsFromMetadata(config["board_url"], string(body))
	// Current rendered configurations use one held document. Paginated rendered
	// routes require their independently tested page navigation contract.
	if err != nil || o.Pagination != nil || o.ExpectedOrganization != "" {
		return fail()
	}
	if revolut {
		if o.Source != "nextdata" || o.Path != "props.pageProps.positions" || o.Template != "https://www.revolut.com/careers/position/{slug}-{id}/" || len(o.SlugFields) != 1 || o.SlugFields[0] != "text" || len(o.Fields) != 3 || o.Fields["title"] != "text" || o.Fields["locations"] != "locations[].name" || o.Fields["metadata.team"] != "team" {
			return fail()
		}
		o.Strict = true
	}
	if florida {
		if o.ExpectedTitle != "" {
			return fail()
		}
		o.BrowserDocumentTransform = "florida-courts"
		o.Strict = true // Browser-expression failures never become an empty inventory.
	}
	if yum {
		if o.ExpectedTitle != "" || o.Path != "jobs" || o.Template != "{link}" || len(o.Fields) == 0 {
			return fail()
		}
		o.BrowserDocumentTransform = "yum-china-http"
		o.Strict = true
	}
	return o, options, nil
}

func NextdataMonitorResourceMatches(p GreenhouseMonitorProfile, config map[string]string, resource string) bool {
	o, err := NextdataMonitorOptions(config)
	return err == nil && p.Provider == "nextdata" && p.Endpoint == o.BoardURL && (o.ResourceMatches(resource) || o.DetailWitnessMatches(resource) || o.BrowserDocumentTransform == "yum-china-http" && (apisniffer.YumChinaResourceScope{}).ResourceMatches(resource))
}

func nextdataMonitorEnrichment(config map[string]string) ([]string, error) {
	return monitorEnrichmentFields(config, map[string]bool{"description": true, "employment_type": true, "locations": true})
}
