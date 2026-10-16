package documentactions

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestActionsRetainCanonicalJSONNumbers(t *testing.T) {
	for _, body := range []string{
		`[{"action":"wait","ms":750,"timeout":1.25}]`,
		`[{"action":"wait_for","selector":".job","timeout":0.125}]`,
		`[{"action":"repeat","selector":"button","max":2,"wait_ms":0.5}]`,
		`[{"action":"paginate_collect","next_selector":"a.next","max_pages":2,"wait_ms":0,"page_size_selector":"select","page_size":100}]`,
		`[{"action":"paginate_collect","page_size_selector":"select","page_size":0}]`,
	} {
		var ordinary, retained any
		if json.Unmarshal([]byte(body), &ordinary) != nil {
			t.Fatal("fixture")
		}
		decoder := json.NewDecoder(strings.NewReader(body))
		decoder.UseNumber()
		if decoder.Decode(&retained) != nil {
			t.Fatal("retained fixture")
		}
		want, err := Parse(ordinary)
		if err != nil {
			t.Fatal("ordinary JSON rejected", err)
		}
		got, err := Parse(retained)
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("opaque profile changed action semantics: %v", err)
		}
	}
}

func TestCanonicalNumbersRetainActionLimits(t *testing.T) {
	for _, body := range []string{
		`[{"action":"wait","ms":-1}]`,
		`[{"action":"wait","ms":120001}]`,
		`[{"action":"wait","timeout":0}]`,
		`[{"action":"wait","timeout":1.0001}]`,
		`[{"action":"repeat","selector":"button","max":1.5}]`,
		`[{"action":"repeat","selector":"button","max":1001}]`,
		`[{"action":"paginate_collect","page_size":100001}]`,
		`[{"action":"paginate_collect","page_size":1.5}]`,
	} {
		var retained any
		decoder := json.NewDecoder(strings.NewReader(body))
		decoder.UseNumber()
		if decoder.Decode(&retained) != nil {
			t.Fatal("fixture")
		}
		if _, err := Parse(retained); err == nil {
			t.Fatal("invalid canonical action number accepted")
		}
	}
	for _, number := range []json.Number{"", "null", `"1"`, "01", "NaN", "Infinity", "1e309", "1 2"} {
		if _, err := Parse([]any{map[string]any{"action": "wait", "ms": number}}); err == nil {
			t.Fatal("invalid JSON number accepted")
		}
	}
}
