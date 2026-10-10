package documentactions

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestParseSequentialPythonDefaultsAndExplicitFailurePolicy(t *testing.T) {
	var raw any
	if json.Unmarshal([]byte(`[{"action":"evaluate","script":"() => window.ready = true","required":true,"timeout":0.125},{"action":"wait"},{"action":"wait","ms":0}]`), &raw) != nil {
		t.Fatal("fixture")
	}
	got, err := Parse(raw)
	if err != nil || len(got) != 3 || !got[0].Required || got[0].TimeoutMS != 125 || got[1].Required || got[1].Milliseconds != 1000 || got[2].Milliseconds != 0 || Budget(got) != 20125*time.Millisecond {
		t.Fatal("defaults or order changed", err)
	}
	for _, fixture := range []string{`[{"action":"click"}]`, `[{"action":"evaluate","script":"x","frame":"iframe"}]`, `[{"action":"wait","required":"false"}]`, `[{"action":"wait","timeout":0}]`, `[{"action":"wait","ms":-1}]`, `[{"action":"evaluate"}]`, `[{"action":"wait","foo":1}]`} {
		json.Unmarshal([]byte(fixture), &raw)
		if _, err := Parse(raw); err == nil {
			t.Fatal("unsupported action admitted", fixture)
		}
	}
	tooMany := make([]Action, MaxActions+1)
	for i := range tooMany {
		tooMany[i] = Action{Kind: "wait", TimeoutMS: 1}
	}
	if Valid(tooMany) {
		t.Fatal("unbounded action count")
	}
	if Valid([]Action{{Kind: "wait", TimeoutMS: 120000}, {Kind: "wait", TimeoutMS: 120000}, {Kind: "wait", TimeoutMS: 120000}, {Kind: "wait", TimeoutMS: 120000}, {Kind: "wait", TimeoutMS: 120000}, {Kind: "wait", TimeoutMS: 120000}}) {
		t.Fatal("unbounded aggregate timeout")
	}
}
func TestRequestBindingAndFullWireBound(t *testing.T) {
	r := Request{Protocol: Protocol, RequestID: strings.Repeat("a", 64), ConfigFingerprint: strings.Repeat("b", 64), Input: []byte{1}, Actions: []Action{{Kind: "wait", TimeoutMS: 10000}}}
	b, e := MarshalBounded(r)
	if e != nil {
		t.Fatal(e)
	}
	var round Request
	if Decode(b, RequestLimit, &round) != nil || !round.Valid() {
		t.Fatal("roundtrip")
	}
	r.Actions = []Action{{Kind: "evaluate", Script: strings.Repeat("s", 16384), TimeoutMS: 1}}
	for i := 0; i < 8; i++ {
		r.Actions = append(r.Actions, r.Actions[0])
	}
	if _, e := MarshalBounded(r); e == nil {
		t.Fatal("aggregate wire limit ignored")
	}
	r.Actions = []Action{{Kind: "wait", TimeoutMS: 1}}
	r.ConfigFingerprint = "changed"
	if r.Valid() {
		t.Fatal("unbound request")
	}
	if strings.Contains(r.String(), "changed") {
		t.Fatal("request diagnostic leaked")
	}
}

func TestRemovalActionsPreserveOrderAndRejectMixedPayloads(t *testing.T) {
	var raw any
	json.Unmarshal([]byte(`[{"action":"remove","selector":".obsolete","required":true},{"action":"dismiss_overlays"}]`), &raw)
	got, err := Parse(raw)
	if err != nil || len(got) != 2 || got[0].Selector != ".obsolete" || !got[0].Required || got[1].Kind != "dismiss_overlays" || got[1].Required || got[1].TimeoutMS != 10000 {
		t.Fatal("removal defaults/order lost", err)
	}
	for _, body := range []string{`[{"action":"remove"}]`, `[{"action":"remove","selector":12}]`, `[{"action":"remove","selector":""}]`, `[{"action":"remove","selector":"a","script":"private"}]`, `[{"action":"dismiss_overlays","selector":"a"}]`} {
		json.Unmarshal([]byte(body), &raw)
		if _, err := Parse(raw); err == nil {
			t.Fatal("invalid removal admitted", body)
		}
	}
	if Valid([]Action{{Kind: "remove", Selector: strings.Repeat("a", 4097), TimeoutMS: 1}}) || Valid([]Action{{Kind: "evaluate", Selector: "a", Script: "private", TimeoutMS: 1}}) {
		t.Fatal("mixed/unbounded selector admitted")
	}
}
