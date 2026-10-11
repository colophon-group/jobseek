package apisniffer

import (
	"net/http"
	"testing"
)

func TestBrowserLiveURLPreservesConfiguredQueryAndRejectsForeignAuthority(t *testing.T) {
	raw := `{"browser":true,"api_url":"https://example.com/token/old/jobs?offset=0&limit=20","api_url_match":"example.com/token/*/jobs","json_path":"items","url_field":"url","pagination":{"style":"offset","location":"query","param_name":"offset","start_value":0,"increment":20}}`
	o, err := BrowserReplayOptionsFromMetadata("https://example.com/careers", raw)
	if err != nil {
		t.Fatal(err)
	}
	exchange, err := NewBrowserReplayExchange("https://example.com/token/new/jobs?offset=100&limit=1", "GET", http.Header{}, []byte(`{"items":[{"url":"https://example.com/job/1"}]}`))
	if err != nil {
		t.Fatal(err)
	}
	fresh, document, matched := SelectLiveBrowserReplayExchange(o, []BrowserReplayExchange{exchange})
	if !matched || document == nil || fresh != "https://example.com/token/new/jobs?offset=0&limit=20" {
		t.Fatal("captured query escaped configured binding")
	}
	mapped, err := o.LiveRequestURL("https://example.com/token/old/jobs?offset=20&limit=20", fresh)
	if err != nil || mapped != "https://example.com/token/new/jobs?offset=20&limit=20" {
		t.Fatal("pagination binding lost", err)
	}
	for _, u := range []string{"https://foreign.example/token/new/jobs", "http://example.com/token/new/jobs", "https://user@example.com/token/new/jobs", "https://example.com/token/new/other", "https://example.com/token/new/jobs#x"} {
		if o.LiveURLMatches(u, "GET") {
			t.Fatal("unbound live resource accepted")
		}
	}
	if _, err := o.LiveRequestURL("https://example.com/token/old/jobs?offset=20&limit=1", fresh); err == nil {
		t.Fatal("non-pagination query mutation accepted")
	}
	if _, err := OptionsFromMetadata("https://example.com/careers", raw); err == nil {
		t.Fatal("HTTP ignored browser fallback")
	}
}
