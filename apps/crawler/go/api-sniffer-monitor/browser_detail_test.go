package apisniffer

import "testing"

func TestBrowserDetailConfigurationPreservesCaptureDefaultsAndRefusesUnimplementedControls(t *testing.T) {
	o, err := BrowserDetailOptionsFromConfig([]byte(`{}`))
	if err != nil || o.Wait != "load" || o.TimeoutMS != 20000 || o.SettleMS != 3000 {
		t.Fatal("original defaults differ", err)
	}
	o, err = BrowserDetailOptionsFromConfig([]byte(`{"fields":{"title":"name"},"json_path":"data.job","wait":"networkidle","timeout":45000,"settle":2.5,"enrich":["description"]}`))
	if err != nil || o.Path != "data.job" || o.Wait != "networkidle" || o.TimeoutMS != 45000 || o.SettleMS != 2500 || o.Fields["title"] != "name" {
		t.Fatal("original configuration differs", err)
	}
	for _, raw := range []string{`{"proxy":true}`, `{"persistent_context":true}`, `{"actions":[]}`, `{"api_url":"https://example.com/api"}`, `{"wait":"unknown"}`, `{"timeout":true}`, `{"settle":30.1}`, `{"settle":0.0001}`, `{"fields":[]}`, `{"json_path":"broken["}`, `{"enrich":["description","description"]}`} {
		if _, err := BrowserDetailOptionsFromConfig([]byte(raw)); err == nil {
			t.Fatal("unsupported configuration admitted", raw)
		}
	}
}
