package queue

import (
	"encoding/json"
	"testing"
)

func TestDetailProfileCannotMutateBoundDOMOptions(t *testing.T) {
	d := &CurrentWorkdayDetail{profile: WorkdayDetailProfile{DOMConfig: map[string]any{"steps": []any{map[string]any{"offset": json.Number("1"), "field": "title"}}}}}
	p := d.Profile()
	step := p.DOMConfig["steps"].([]any)[0].(map[string]any)
	if step["offset"] != json.Number("1") {
		t.Fatal("integer extraction option lost its original type")
	}
	step["offset"] = json.Number("9")
	p.DOMConfig["scope"] = ".unbound"
	if d.profile.DOMConfig["scope"] != nil || d.Profile().DOMConfig["steps"].([]any)[0].(map[string]any)["offset"] != json.Number("1") {
		t.Fatal("detached evidence changed the opaque attempt's extraction options")
	}
}
