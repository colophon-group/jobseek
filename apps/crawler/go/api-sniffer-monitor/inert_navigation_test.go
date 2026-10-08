package apisniffer

import (
	"encoding/json"
	"reflect"
	"testing"
)

// Python selects API browser execution from browser/api_url; render is a
// legacy annotation on these explicit API configurations.
func TestExplicitAPIInertNavigationPreservesInventoryAndTransport(t *testing.T) {
	board := "https://example.com/careers"
	md := map[string]any{"api_url": "https://example.com/api", "json_path": "jobs", "url_template": "https://example.com/jobs/{id}", "fields": map[string]any{"title": "title"}}
	encode := func() string { b, _ := json.Marshal(md); return string(b) }
	original, e := OptionsFromMetadata(board, encode())
	if e != nil {
		t.Fatal(e)
	}
	for _, flag := range []any{nil, false, true} {
		md["render"] = flag
		md["resource_policy"] = "none"
		got, e := OptionsFromMetadata(board, encode())
		if e != nil || !reflect.DeepEqual(original, got) {
			t.Fatal("inert explicit API annotation changed execution", flag, e)
		}
	}
	for _, policy := range []any{"auto", "lean", false, 1, map[string]any{}, []any{}, " none"} {
		md["resource_policy"] = policy
		if _, e := OptionsFromMetadata(board, encode()); e == nil {
			t.Fatal("unimplemented resource controller accepted", policy)
		}
	}
	delete(md, "resource_policy")
	md["render"] = true
	for _, key := range []string{"browser", "proxy", "skip_ssl"} {
		md[key] = true
		if _, e := OptionsFromMetadata(board, encode()); e == nil {
			t.Fatal("active transport override lost", key)
		}
		delete(md, key)
	}
	md["browser"] = true
	md["resource_policy"] = "none"
	browser, e := BrowserReplayOptionsFromMetadata(board, encode())
	if e != nil || !reflect.DeepEqual(original, browser.Inventory) {
		t.Fatal("browser replay inventory differs", e)
	}
}
