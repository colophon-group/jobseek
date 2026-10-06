package apisniffer

import (
	"encoding/json"
	"fmt"
	"testing"
)

func TestFourthProviderOptionsBoundEndpointsBootstrapAndDefaults(t *testing.T) {
	source := "https://www.paycomonline.net/v4/ats/web.php/portal/11111111111111111111111111111111/career-page"
	o, e := PaycomOptionsFromMetadata(source, `{}`)
	if e != nil {
		t.Fatal(e)
	}
	for _, raw := range []string{`{"token":"22222222222222222222222222222222"}`, `{"token":true}`, `{"proxy":true}`, `{"ssl_verify":false}`, `{"render":true}`} {
		if _, e := PaycomOptionsFromMetadata(source, raw); e == nil {
			t.Fatal("unsupported identity/transport admitted", raw)
		}
	}
	for _, s := range []string{"https://api.paycomonline.net/api/ats/job-posting-previews/search", o.PortalURL()} {
		if !o.ResourceMatches(s) {
			t.Fatal("valid source refused")
		}
	}
	for _, s := range []string{"https://api.paycomonline.net.evil.com/api/ats/job-posting-previews/search", "https://api.paycomonline.net/api/ats/job-posting-previews/search?token=x", o.JobURL("123"), "http://api.paycomonline.net/api/ats/job-posting-previews/search"} {
		if o.ResourceMatches(s) {
			t.Fatal("request escaped compiled scope", s)
		}
	}
	for _, service := range []string{"https://example.com", "https://api.paycomonline.net@evil.com", "https://api.paycomonline.net:444", "https://api.paycomonline.net?token=x", "http://api.paycomonline.net"} {
		lib, _ := json.Marshal(map[string]any{"atsPortalMantleServiceUrl": service})
		body, _ := json.Marshal(map[string]any{"sessionJWT": "public.fixture.signature", "libConfig": string(lib)})
		if _, e := PaycomExtractBootstrap("var configsFromHost = "+string(body)+";", o); e == nil {
			t.Fatal("untrusted bootstrap service admitted", service)
		}
	}
	for _, defaults := range []map[string]any{{"defaults": map[string]any{"locations": []any{}}}, {"defaults": map[string]any{"locations": []any{""}}}, {"defaults": map[string]any{"locations": []any{"Zurich"}, "title": "X"}}} {
		if _, e := PaycomDefaultLocations(defaults); e == nil {
			t.Fatal("invalid defaults admitted")
		}
	}
	r, e := RipplingOptionsFromMetadata("https://ats.us1.rippling.com/en-US/acme/jobs", `{}`)
	if e != nil || r.Slug != "acme" {
		t.Fatal("regional identity lost", e)
	}
	for _, s := range []string{r.JobURL("123"), r.ListingURL() + "/123", r.ListingURL() + "?extra=1", "https://example.com/"} {
		if r.ResourceMatches(s) {
			t.Fatal("list profile acquired another resource")
		}
	}
	for _, raw := range []string{`{"slug":"../evil"}`, `{"slug":true}`, `{"proxy":true}`, `{"render":true}`} {
		if _, e := RipplingOptionsFromMetadata("https://ats.rippling.com/acme/jobs", raw); e == nil {
			t.Fatal("invalid Rippling configuration admitted", raw)
		}
	}
}
func TestRipplingCapCountsReferenceRowsBeforeDeduplication(t *testing.T) {
	for _, n := range []int{50000, 50001} {
		rows := make([]any, n)
		for i := range rows {
			rows[i] = map[string]any{"uuid": "same-id"}
		}
		urls, truncated, e := RipplingListing(&Document{Value: rows}, RipplingOptions{"acme"})
		if e != nil || len(urls) != 1 || truncated != (n > 50000) {
			t.Fatal(fmt.Sprintf("cap/dedup contract lost for %d rows", n))
		}
	}
}
