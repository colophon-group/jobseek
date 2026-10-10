package apisniffer

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
)

func TestPostDataRefreshKeepsOriginalOrderedFieldsAndAtomicFailure(t *testing.T) {
	base := `{"api_url":"https://example.com/api","method":"POST","post_data":"action=jobs&nonce=old&nonce=other&page=1","json_path":"jobs","url_template":"https://example.com/jobs/{id}","fields":{"title":"title"},"post_data_refresh":{"fields":{"nonce":"nonce=([a-z]+)"}}}`
	for _, mode := range []string{"complete", "missing", "empty", "oversize", "nontext", "bootstrap-error", "cancelled"} {
		t.Run(mode, func(t *testing.T) {
			o, err := OptionsFromMetadata("https://example.com/careers", base)
			if err != nil {
				t.Fatal(err)
			}
			original := o.Body
			calls := []Request{}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			out, err := Discover(ctx, o, func(ctx context.Context, r Request) (*Document, error) {
				calls = append(calls, r)
				if len(calls) == 1 {
					if !o.IsPostDataRefreshRequest(r) || !o.ResourceMatches(r.URL) {
						t.Fatal("fresh public bootstrap scope changed")
					}
					if mode == "bootstrap-error" {
						return nil, ErrInventory
					}
					if mode == "cancelled" {
						cancel()
						return nil, ctx.Err()
					}
					html := "nonce=fresh"
					switch mode {
					case "missing":
						html = "no nonce"
					case "empty":
						html = "nonce="
					case "oversize":
						html = "nonce=" + strings.Repeat("x", 16385)
					case "nontext":
						return &Document{Value: map[string]any{}}, nil
					}
					return &Document{Value: html}, nil
				}
				if mode != "complete" || r.Method != "POST" || r.Body != "action=jobs&nonce=fresh&nonce=fresh&page=1" {
					t.Fatal("original ordered POST refresh changed")
				}
				return Decode([]byte(`{"jobs":[{"id":"1","title":"Engineer"}]}`))
			}, func(base, raw string) (string, error) { return raw, nil })
			if o.Body != original {
				t.Fatal("captured options mutated")
			}
			if mode == "complete" {
				if err != nil || len(out.Jobs) != 1 || out.Truncated || len(calls) != 2 {
					t.Fatal("complete refreshed inventory changed", err)
				}
			} else if err == nil || len(out.Jobs) != 0 || len(calls) != 1 {
				t.Fatal("failed bootstrap acquired prefix authority", err)
			}
		})
	}
}

func TestPostDataRefreshValidationAndOriginalBodyProjection(t *testing.T) {
	for _, raw := range []string{`{"fields":{"nonce":"(x)(y)"}}`, `{"fields":{"nonce":"["}}`, `{"fields":{}}`, `{"fields":{"nonce":"(x)"},"source_url":"https://foreign.example/page"}`, `{"fields":{"nonce":"(x)"},"extra":1}`} {
		metadata := fmt.Sprintf(`{"api_url":"https://example.com/api","method":"POST","json_path":"jobs","post_data":"nonce=old","post_data_refresh":%s}`, raw)
		if _, err := OptionsFromMetadata("https://example.com/careers", metadata); err == nil {
			t.Fatal("unqualified POST refresh configuration admitted")
		}
	}
	for _, body := range []string{`{"outer":{"nonce":"old"},"page":1}`, `nonce=old&z=&nonce=old`} {
		field := "nonce"
		if strings.HasPrefix(body, "{") {
			field = "outer.nonce"
		}
		md, _ := json.Marshal(map[string]any{"api_url": "https://example.com/api", "method": "POST", "json_path": "jobs", "post_data": body, "post_data_refresh": map[string]any{"fields": map[string]any{field: "token=([a-z]+)"}}})
		o, err := OptionsFromMetadata("https://example.com/careers", string(md))
		if err != nil {
			t.Fatal(err)
		}
		updated, err := o.refreshedPostData("token=fresh")
		if err != nil || updated.Body == body {
			t.Fatal("original body field refresh failed", err)
		}
		if strings.HasPrefix(body, "{") {
			a, _ := Decode([]byte(updated.Body))
			b, _ := Decode([]byte(`{"outer":{"nonce":"fresh"},"page":1}`))
			if !reflect.DeepEqual(a.Value, b.Value) {
				t.Fatal("original JSON path changed")
			}
		} else if updated.Body != "nonce=fresh&z=&nonce=fresh" {
			t.Fatal("ordered duplicate form fields changed")
		}
	}
}
