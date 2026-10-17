package main

import (
	"encoding/json"
	"os"
	"reflect"
	"testing"
)

func TestFrozenPythonImpactRankingAndIdentity(t *testing.T) {
	raw, e := os.ReadFile("testdata/python_models.json")
	var f struct {
		Input            []companyImpact
		RankedSourceKeys []string `json:"ranked_source_keys"`
		SourceKeys       []string `json:"source_keys"`
	}
	if e != nil || json.Unmarshal(raw, &f) != nil {
		t.Fatal("original model fixture")
	}
	for i, c := range f.Input {
		if c.sourceKey() != f.SourceKeys[i] {
			t.Fatalf("original identity differs at%d", i)
		}
	}
	ranked := (impactSnapshot{Companies: f.Input}).ranked()
	keys := []string{}
	for _, c := range ranked {
		keys = append(keys, c.sourceKey())
	}
	if !reflect.DeepEqual(keys, f.RankedSourceKeys) {
		t.Fatal("original ranking differs")
	}
	if len(f.Input) != len(ranked) || f.Input[0].Slug != "fixture-0" {
		t.Fatal("ranking mutated original ordering")
	}
}
func TestCompatibilityRegistryConstants(t *testing.T) {
	registry, e := loadCompatibility()
	if e != nil || len(registry) != 47 {
		t.Fatal("original compatibility registry", e)
	}
	for _, name := range []string{"infojobs_es", "jobbankca", "jobs_cz", "seek"} {
		if registry[name].candidateEligible() || registry[name].Seedable || registry[name].MonitorType != nil {
			t.Fatal("excluded marketplace became a company candidate")
		}
	}
	for name, preset := range map[string]string{"successfactors": "successfactors", "teamtailor": "teamtailor"} {
		c := registry[name]
		config, e := c.monitorConfig()
		if e != nil || c.MonitorType == nil || *c.MonitorType != "rss" || config["preset"] != preset || !c.Seedable {
			t.Fatal("original shared RSS compatibility changed")
		}
	}
	if registry["mercor"].Seedable {
		t.Fatal("unverified first-party API config became seedable")
	}
}
