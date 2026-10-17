package queue

import (
	"encoding/json"
	"maps"
	"reflect"
	"testing"
)

func TestDOMLegacyAliasesRetainOriginalIgnoredSemanticsAndBinding(t *testing.T) {
	c := domMonitorConfig()
	base, err := InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal(err)
	}
	options, err := DOMMonitorOptions(c)
	if err != nil {
		t.Fatal(err)
	}
	for _, value := range []any{nil, "https://unrelated.example/jobs", true, 4, []any{"ignored"}, map[string]any{"ignored": true}} {
		var md map[string]any
		if err := json.Unmarshal([]byte(c["metadata"]), &md); err != nil {
			t.Fatal(err)
		}
		md["url"], md["job_link_pattern"] = value, value
		raw, err := json.Marshal(md)
		if err != nil {
			t.Fatal(err)
		}
		changed := maps.Clone(c)
		changed["metadata"] = string(raw)
		p, err := InspectRichMonitor(profileBoardID, changed)
		if err != nil || p.Endpoint != base.Endpoint || p.Profile != base.Profile || p.EffectiveConfigSHA256 == base.EffectiveConfigSHA256 {
			t.Fatal("ignored aliases changed the fetch route or escaped full binding", err)
		}
		actual, err := DOMMonitorOptions(changed)
		if err != nil || !reflect.DeepEqual(actual, options) {
			t.Fatal("ignored aliases changed inventory parsing", err)
		}
	}
}
