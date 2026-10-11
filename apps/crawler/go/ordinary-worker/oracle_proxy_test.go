package worker

import (
	"encoding/json"
	"testing"
)

func oracleProxyFixtureMetadata(t *testing.T, body string, monitor bool) string {
	t.Helper()
	var md map[string]json.RawMessage
	if json.Unmarshal([]byte(body), &md) != nil {
		t.Fatal("invalid Oracle fixture")
	}
	if monitor {
		md["proxy"] = json.RawMessage(`true`)
	}
	options := map[string]json.RawMessage{}
	if raw := md["scraper_config"]; raw != nil && json.Unmarshal(raw, &options) != nil {
		t.Fatal("invalid separate Oracle detail fixture")
	}
	options["proxy"] = json.RawMessage(`true`)
	md["scraper_config"], _ = json.Marshal(options)
	out, e := json.Marshal(md)
	if e != nil {
		t.Fatal(e)
	}
	return string(out)
}
