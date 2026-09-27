package main

import (
	"crypto/sha256"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

//go:embed registry_routes.json
var registryRoutesJSON []byte

type registryRoutes struct {
	APIMonitors    []string        `json:"api_monitors"`
	Scrapers       map[string]bool `json:"scrapers"`
	RenderScrapers []string        `json:"render_scrapers"`
}

func loadRegistryRoutes() (registryRoutes, error) {
	var routes registryRoutes
	err := json.Unmarshal(registryRoutesJSON, &routes)
	return routes, err
}

// Configuration values retain Python truth semantics, including empty maps,
// arrays and integral/fractional JSON numbers. They are not Go booleans only.
func registryTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		f, err := v.Float64()
		return err != nil || f != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}

func registryMonitorBrowser(name string, config map[string]any) bool {
	switch name {
	case "accenture", "brassring", "bytedance", "candidatus", "darwinbox", "dayforce", "njoyn":
		return true
	case "api_sniffer":
		return !registryTruthy(config["api_url"]) || registryTruthy(config["browser"])
	case "dom", "inline", "rss":
		return registryTruthy(config["render"])
	case "nextdata":
		return registryTruthy(config["render"]) || registryTruthy(config["actions"]) || config["source"] == "browser" || registryTruthy(config["browser_expression"])
	default:
		return false
	}
}

func registryScraperBrowser(name string, config map[string]any, routes registryRoutes) bool {
	for name != "" {
		if browser, registered := routes.Scrapers[name]; registered {
			if browser && !registryTruthy(config["api_url"]) {
				return true
			}
			if !browser && registryTruthy(config["render"]) {
				for _, render := range routes.RenderScrapers {
					if name == render {
						return true
					}
				}
			}
		}
		fallback, ok := config["fallback"].(map[string]any)
		if !ok {
			return false
		}
		name, _ = fallback["type"].(string)
		config, _ = fallback["config"].(map[string]any)
	}
	return false
}

func registryConfigObject(raw string) (map[string]any, error) {
	if raw == "" {
		return map[string]any{}, nil
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.UseNumber()
	var object map[string]any
	if err := decoder.Decode(&object); err != nil || object == nil {
		return nil, errors.New("board configuration must be a JSON object")
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, errors.New("trailing board configuration")
	}
	return object, nil
}

func registryBoardMetadata(row map[string]*string) (map[string]any, error) {
	metadata, err := registryConfigObject(registryText(row, "monitor_config"))
	if err != nil {
		return nil, err
	}
	if scraper := registryText(row, "scraper_type"); scraper != "" {
		metadata["scraper_type"] = scraper
	}
	if raw := registryText(row, "scraper_config"); raw != "" {
		config, err := registryConfigObject(raw)
		if err != nil {
			return nil, err
		}
		metadata["scraper_config"] = config
	}
	monitorConfig := map[string]any{}
	for key, value := range metadata {
		switch key {
		case "scraper_type", "scraper_config", "identity_migration", "_identity_migration_receipt", "_monitor_config_fingerprint":
			continue
		}
		monitorConfig[key] = value
	}
	payload, err := taxonomyCanonicalJSON(map[string]any{"board_url": registryText(row, "board_url"), "monitor_type": registryText(row, "monitor_type"), "monitor_config": monitorConfig}, true)
	if err != nil {
		return nil, err
	}
	metadata["_monitor_config_fingerprint"] = fmt.Sprintf("%x", sha256.Sum256(payload))
	return metadata, nil
}
