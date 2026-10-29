package apisniffer

import (
	"context"
	"encoding/json"
	"net/url"
	"reflect"
	"testing"
)

func TestPublicAuraContextRefreshBeforeInventory(t *testing.T) {
	for _, mode := range []string{"numeric-zero", "numeric-one", "boolean", "wrong-application", "missing-framework", "invalid-loaded", "invalid-uad"} {
		t.Run(mode, func(t *testing.T) {
			bootstrap := map[string]any{"mode": "PROD", "app": "siteforce:communityApp", "fwuid": "current-framework", "loaded": map[string]any{"APPLICATION@markup://siteforce:communityApp": "current-definition"}, "uad": 0, "bootstrapOnly": true, "globals": map[string]any{"unexpected": "ignored"}}
			valid := true
			switch mode {
			case "numeric-one":
				bootstrap["uad"] = 1
			case "boolean":
				bootstrap["uad"] = false
			case "wrong-application":
				bootstrap["app"] = "other:app"
				valid = false
			case "missing-framework":
				delete(bootstrap, "fwuid")
				valid = false
			case "invalid-loaded":
				bootstrap["loaded"] = []any{}
				valid = false
			case "invalid-uad":
				bootstrap["uad"] = 2
				valid = false
			}
			text, _ := json.Marshal(bootstrap)
			md, _ := json.Marshal(map[string]any{"api_url": "https://fixture.example/s/sfsites/aura", "method": "POST", "post_data": "message=fixture&aura.context=refresh-required", "json_path": "actions[0].returnValue", "url_template": "https://fixture.example/s/offer/{id}", "fields": map[string]any{"title": "title"}, "post_data_refresh": map[string]any{"aura_context": true, "fields": map[string]any{"aura.context": `publicContext=(\{.*\})`}}})
			o, err := OptionsFromMetadata("https://fixture.example/s/jobs", string(md))
			if err != nil {
				t.Fatal(err)
			}
			original := o.Body
			calls := 0
			out, err := Discover(context.Background(), o, func(ctx context.Context, r Request) (*Document, error) {
				calls++
				if calls == 1 {
					if !o.IsPostDataRefreshRequest(r) {
						t.Fatal("public bootstrap not first")
					}
					return &Document{Value: "publicContext=" + string(text)}, nil
				}
				if !valid {
					t.Fatal("invalid bootstrap reached API")
				}
				form, e := url.ParseQuery(r.Body)
				if e != nil || form.Get("message") != "fixture" {
					t.Fatal("fixed request changed")
				}
				var got map[string]any
				if json.Unmarshal([]byte(form.Get("aura.context")), &got) != nil {
					t.Fatal("request context is not JSON")
				}
				expected := map[string]any{}
				for _, key := range []string{"mode", "app", "fwuid", "loaded", "uad"} {
					expected[key] = bootstrap[key]
				}
				expected["dn"] = []any{}
				expected["globals"] = map[string]any{}
				raw, _ := json.Marshal(expected)
				var normalized map[string]any
				json.Unmarshal(raw, &normalized)
				if !reflect.DeepEqual(got, normalized) {
					t.Fatal("request context copied bootstrap-only state or lost client fields")
				}
				return Decode([]byte(`{"actions":[{"returnValue":[{"id":"1","title":"Engineer"}]}]}`))
			}, func(base, raw string) (string, error) { return raw, nil })
			if o.Body != original {
				t.Fatal("refresh mutated reusable options")
			}
			if valid {
				if err != nil || calls != 2 || len(out.Jobs) != 1 {
					t.Fatal("refreshed inventory failed", err)
				}
			} else if err == nil || calls != 1 || len(out.Jobs) != 0 {
				t.Fatal("invalid bootstrap acquired inventory authority")
			}
		})
	}
}

func TestAuraContextRefreshRequiresExplicitCompatibleForm(t *testing.T) {
	for _, mode := range []string{"false", "wrong-field", "wrong-endpoint"} {
		t.Run(mode, func(t *testing.T) {
			refresh := map[string]any{"aura_context": true, "fields": map[string]any{"aura.context": "context=(.*)"}}
			md := map[string]any{"api_url": "https://fixture.example/s/sfsites/aura", "method": "POST", "post_data": "aura.context=required", "json_path": "jobs", "post_data_refresh": refresh}
			switch mode {
			case "false":
				refresh["aura_context"] = false
			case "wrong-field":
				refresh["fields"] = map[string]any{"nonce": "context=(.*)"}
			case "wrong-endpoint":
				md["api_url"] = "https://fixture.example/api/jobs"
			}
			raw, _ := json.Marshal(md)
			if _, err := OptionsFromMetadata("https://fixture.example/s/jobs", string(raw)); err == nil {
				t.Fatal("incompatible context refresh admitted")
			}
		})
	}
}
