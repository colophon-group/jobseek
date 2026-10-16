package queue

import (
	"encoding/json"
	actions "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/documentactions"
	"reflect"
	"testing"
)

func TestDetachedDetailProfileRetainsExecutableActions(t *testing.T) {
	original := map[string]any{"actions": []any{map[string]any{"action": "wait", "ms": float64(750), "timeout": float64(1.25)}}}
	for _, api := range []bool{false, true} {
		profile := WorkdayDetailProfile{Profile: domRenderedDetailProfile, DOMConfig: original}
		if api {
			profile.Profile = browserAPIDetailProfile
			profile.DOMConfig = nil
			profile.HTTPAPIConfig = original
		}
		detached := (&CurrentWorkdayDetail{profile: profile}).Profile()
		options := detached.DOMConfig
		if api {
			options = detached.HTTPAPIConfig
		}
		if _, ok := options["actions"].([]any)[0].(map[string]any)["ms"].(json.Number); !ok {
			t.Fatal("retained numeric representation changed")
		}
		want, err := actions.Parse(original["actions"])
		if err != nil {
			t.Fatal(err)
		}
		got, err := actions.Parse(options["actions"])
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("validated action rejected after opaque profile copy: %v", err)
		}
	}
}
