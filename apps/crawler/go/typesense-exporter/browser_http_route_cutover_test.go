package main

import (
	"testing"
)

func TestBrowserHTTPRouteContractsBindConfigurationAndReverse(t *testing.T) {
	for _, target := range browserHTTPRouteTargets() {
		t.Run(target.Slug, func(t *testing.T) {
			forward := map[string]any{}
			for key, value := range target.StaticConfigs.Forward {
				forward[key] = value
			}
			if !target.StaticConfigs.matches(target.LegacyType, forward, false) {
				t.Fatal("committed HTTP config differs")
			}
			if !target.StaticConfigs.matches(target.LegacyType, target.StaticConfigs.Reverse, true) || target.StaticConfigs.matches(target.LegacyType, forward, true) || target.StaticConfigs.matches("api_sniffer", forward, false) {
				t.Fatal("reverse config or type binding differs")
			}
			forward["unproved"] = true
			if target.StaticConfigs.matches(target.LegacyType, forward, false) {
				t.Fatal("unproved config acquired route authority")
			}
		})
	}
}
