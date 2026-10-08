package apisniffer

import (
	"context"
	"errors"
	"testing"
)

func TestHTMLInventoryRejectsUnprovenControlsAndSchemaDrift(t *testing.T) {
	for _, extra := range []string{`,"fields":{"title":"title"}`, `,"url_field":"url"`, `,"item_filter":{"include":{"kind":["job"]}}`, `,"json_path_values":true`, `,"pagination":{"param_name":"page","style":"cumulative_limit"}`, `,"url_regex":"no capture"`} {
		if _, err := OptionsFromMetadata("https://jobs.example/", `{"api_url":"https://api.example/list","json_path":"html","url_regex":"href=\"([^\"]+)\""`+extra+`}`); err == nil {
			t.Fatal("unimplemented HTML contract admitted", extra)
		}
	}
	o, err := OptionsFromMetadata("https://jobs.example/", `{"api_url":"https://api.example/list","json_path":"html","pagination":{"param_name":"page","max_pages":2}}`)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := BrowserReplayOptionsFromMetadata(o.BoardURL, `{"api_url":"https://api.example/list","json_path":"html","browser":true}`); err == nil {
		t.Fatal("HTML browser traversal admitted without its original contract")
	}
	join := func(_, ref string) (string, error) { return ref, nil }
	for _, body := range []string{`{"html":[]}`, `{"different":"schema"}`, `{"html":{}}`} {
		d, _ := Decode([]byte(body))
		got, err := Discover(context.Background(), o, func(context.Context, Request) (*Document, error) { return d, nil }, join)
		if err == nil || len(got.Jobs) != 0 {
			t.Fatal("schema drift established disappearance")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	calls := 0
	got, err := Discover(ctx, o, func(context.Context, Request) (*Document, error) {
		calls++
		if calls == 2 {
			cancel()
		}
		return Decode([]byte(`{"html":"<a href='/jobs/one'>one</a>"}`))
	}, join)
	if !errors.Is(err, context.Canceled) || len(got.Jobs) != 0 {
		t.Fatal("canceled partial inventory survived", got, err)
	}
}
