package main

import (
	_ "embed"
	"encoding/json"
	"fmt"
)

//go:embed compatibility.json
var compatibilityDocument []byte

type compatibility struct {
	MonitorType       *string `json:"monitor_type"`
	Kind              string  `json:"kind"`
	MonitorConfigJSON *string `json:"monitor_config_json"`
	Seedable          bool    `json:"seedable"`
	Reason            *string `json:"reason"`
}

func (c compatibility) candidateEligible() bool { return c.Kind != "excluded" }
func (c compatibility) monitorConfig() (map[string]any, error) {
	if c.MonitorConfigJSON == nil {
		return nil, nil
	}
	var result map[string]any
	if err := json.Unmarshal([]byte(*c.MonitorConfigJSON), &result); err != nil || result == nil {
		return nil, fmt.Errorf("compatibility config must encode an object")
	}
	return result, nil
}

func loadCompatibility() (map[string]compatibility, error) {
	var result map[string]compatibility
	if err := json.Unmarshal(compatibilityDocument, &result); err != nil {
		return nil, err
	}
	for name, c := range result {
		if name == "" || c.Kind != "native" && c.Kind != "generic" && c.Kind != "excluded" {
			return nil, fmt.Errorf("invalid compatibility registry")
		}
		if _, err := c.monitorConfig(); err != nil {
			return nil, err
		}
	}
	return result, nil
}
