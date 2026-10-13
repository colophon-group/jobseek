package apisniffer

import (
	"context"
	"testing"
	"time"
)

func TestExpandedInlineIdentitiesConserveCompleteCards(t *testing.T) {
	o, err := InlineDocumentOptions(map[string]any{"steps": []any{map[string]any{"tag": "p", "field": "title", "match_regex": ".+"}}, "item_boundary_tag": "jobseek-inline-detail"})
	if err != nil {
		t.Fatal(err)
	}
	source := `<jobseek-inline-detail>__inline_detail_boundary__<p>First</p></jobseek-inline-detail><jobseek-inline-detail>__inline_detail_boundary__<p>Second</p></jobseek-inline-detail>`
	for _, c := range []struct {
		name  string
		ids   []string
		valid bool
	}{
		{"complete", []string{"provider-1", "provider-2"}, true},
		{"missing", []string{"provider-1"}, false},
		{"extra", []string{"provider-1", "provider-2", "provider-3"}, false},
		{"duplicate", []string{"provider-1", "provider-1"}, false},
		{"empty", nil, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			got, err := ParseExpandedInlineDocument(context.Background(), source, "https://example.com/careers", o, time.Now(), c.ids)
			if (err == nil) != c.valid {
				t.Fatal("card/identity conservation differs", err)
			}
			if !c.valid {
				if len(got.Jobs) != 0 {
					t.Fatal("failed expansion retained prefix")
				}
				return
			}
			if got.Truncated || len(got.Jobs) != 2 || got.Jobs[0].URL != "https://example.com/careers?_jid=provider-1" || got.Jobs[1].URL != "https://example.com/careers?_jid=provider-2" {
				t.Fatal("trusted provider identities differ")
			}
		})
	}
}

func TestExpandedInlineMissingTitleCannotShiftProviderIdentity(t *testing.T) {
	o, err := InlineDocumentOptions(map[string]any{"steps": []any{map[string]any{"tag": "p", "field": "title", "match_regex": ".+"}}, "item_boundary_tag": "jobseek-inline-detail"})
	if err != nil {
		t.Fatal(err)
	}
	source := `<jobseek-inline-detail>__inline_detail_boundary__<span>Missing title</span></jobseek-inline-detail><jobseek-inline-detail>__inline_detail_boundary__<p>Second</p></jobseek-inline-detail>`
	got, err := ParseExpandedInlineDocument(context.Background(), source, "https://example.com/careers", o, time.Now(), []string{"provider-1", "provider-2"})
	if err == nil || len(got.Jobs) != 0 {
		t.Fatal("missing title gained shifted identity or prefix authority")
	}
}
