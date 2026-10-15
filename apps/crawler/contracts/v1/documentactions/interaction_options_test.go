package documentactions

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestInteractionDefaultsAndMandatoryCollector(t *testing.T) {
	var raw any
	if json.Unmarshal([]byte(`[{"action":"click","selector":"button"},{"action":"wait_for","selector":"a[href]","state":"attached"},{"action":"repeat","selector":"#more"}]`), &raw) != nil {
		t.Fatal("fixture")
	}
	got, err := Parse(raw)
	json.Unmarshal([]byte(`[{"action":"paginate_collect","required":false,"page_size_selector":"select","page_size":100}]`), &raw)
	collector, collectorErr := Parse(raw)
	if err != nil || collectorErr != nil {
		t.Fatal("original defaults rejected", err, collectorErr)
	}
	got = append(got, collector...)
	if err != nil || len(got) != 4 || got[0].Required || got[1].State != "attached" || got[2].Maximum != 50 || got[2].WaitMS != 2000 || got[2].TimeoutMS != 300000 || got[2].Required || !got[3].Required || got[3].MaxPages != 50 || got[3].WaitMS != 5000 || got[3].PageSize != "100" {
		t.Fatal("original interaction order, defaults or collector failure policy changed", err)
	}
	for _, body := range []string{`[{"action":"wait_for","selector":"a","state":"unknown"}]`, `[{"action":"repeat","selector":"a","frame":"iframe"}]`, `[{"action":"repeat","selector":"a:visible span"}]`, `[{"action":"repeat","selector":"a","max":0}]`, `[{"action":"repeat","selector":"a","max":1.5}]`, `[{"action":"repeat","selector":"a","wait_ms":-1}]`, `[{"action":"click","selector":"a","script":"private"}]`, `[{"action":"paginate_collect","max_pages":1001}]`} {
		json.Unmarshal([]byte(body), &raw)
		if _, err := Parse(raw); err == nil {
			t.Fatal("unqualified or mixed interaction admitted")
		}
	}
	if Valid([]Action{{Kind: "paginate_collect", NextSelector: "a.next", MaxPages: 1, TimeoutMS: 1}}) {
		t.Fatal("wire input weakened mandatory collection")
	}
	for _, selector := range []string{`div:has-text("Next") button`, `button:has-text("Next"), a`, `button:has-text("Next"):has-text("Page")`} {
		if interactionSelector(selector, false) {
			t.Fatal("ancestor, union or multiple text predicates admitted")
		}
	}
	for _, value := range []struct{ input, want string }{{`0`, ""}, {`"0"`, "0"}} {
		json.Unmarshal([]byte(`[{"action":"paginate_collect","page_size_selector":"select","page_size":`+value.input+`}]`), &raw)
		parsed, err := Parse(raw)
		if err != nil || parsed[0].PageSize != value.want {
			t.Fatal("original numeric/string zero page-size guard changed")
		}
	}
}

func TestTerminalVisibleInteractionSelectors(t *testing.T) {
	for _, selector := range []string{`#loadmore .button:visible`, `button[aria-label="Load more jobs"]:visible`, `button:has-text("Load more"):visible`} {
		var raw any
		body, _ := json.Marshal([]map[string]any{{"action": "repeat", "selector": selector, "max": 10}})
		if json.Unmarshal(body, &raw) != nil {
			t.Fatal("fixture")
		}
		parsed, err := Parse(raw)
		if err != nil || len(parsed) != 1 || parsed[0].Selector != selector {
			t.Fatal("terminal visibility predicate lost", err)
		}
	}
	for _, selector := range []string{`:visible`, `a:visible span`, `a:visible:visible`, `a,b:visible`, `a:visible, b`, strings.Repeat("a", 4090) + ":visible"} {
		if interactionSelector(selector, false) {
			t.Fatal("ancestor, union or repeated visibility predicate admitted")
		}
	}
}

func TestWaitStatesRetainOriginalDefaultAndWireBounds(t *testing.T) {
	for _, state := range []string{"attached", "detached", "visible", "hidden", ""} {
		step := map[string]any{"action": "wait_for", "selector": ".job"}
		if state != "" {
			step["state"] = state
		}
		parsed, err := Parse([]any{step})
		want := state
		if want == "" {
			want = "visible"
		}
		if err != nil || len(parsed) != 1 || parsed[0].State != want || !Valid(parsed) {
			t.Fatal("wait state rejected", state, err)
		}
	}
}
