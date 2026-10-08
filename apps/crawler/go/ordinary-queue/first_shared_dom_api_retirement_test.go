package queue

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func firstSharedDOMAPIFixture(t *testing.T, variant string) firstOwnerFixture {
	parts := strings.Split(variant, "/")
	kind, route := parts[0], parts[1]
	provider := "dom"
	if strings.HasPrefix(kind, "api-") {
		provider = "api_sniffer"
	}
	var p firstOwnerFixture
	if route == "rendered" {
		p = firstRenderedMonitorFixture(t)
	} else {
		base := provider
		if route == "proxy" {
			base += "/proxy"
		}
		p = firstProviderBatchFixture(t, base)
	}
	md := map[string]any{"scraper_type": "skip"}
	if provider == "api_sniffer" {
		md["api_url"], md["json_path"], md["url_field"] = "https://example.com/api", "jobs", "url"
		if kind == "api-html" {
			md["json_path"] = "html"
			delete(md, "url_field")
		}
		if kind == "api-inert" {
			md["render"] = true
		}
		if kind == "api-boundary" {
			md["url_allowlist"] = `^https://example\.com/jobs/[^/]+$`
		}
	}
	if kind == "dom-none" || kind == "api-none" {
		md["resource_policy"] = "none"
	}
	if kind == "dom-jsonld" {
		md["require_jsonld_jobposting"] = true
		md["link_selector"] = "a.job"
	}
	if kind == "dom-include" {
		md["include_board_url"] = true
	}
	if kind == "dom-group" {
		md["link_selector"] = "h3 a[href], h4 a[href]"
	}
	if route == "proxy" {
		md["proxy"] = true
	}
	if route == "rendered" {
		if provider == "api_sniffer" {
			md["browser"] = true
		} else {
			md["render"] = true
		}
	}
	raw, _ := json.Marshal(md)
	ctx := context.Background()
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type=$2,board_url='https://example.com/careers',metadata=$3::jsonb WHERE id=$1::uuid", p.f.task.ID, provider, string(raw)); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "crawler_type", provider, "board_url", "https://example.com/careers", "metadata", string(raw)).Err(); e != nil {
		t.Fatal(e)
	}
	plan, e := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if e != nil {
		t.Fatal("shared variant stage", e)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}
func TestRealSharedNavigationHTMLVariantColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api-html/direct", "api-html/proxy", "api-inert/direct", "api-none/direct", "api-none/proxy", "api-none/rendered", "dom-none/direct", "dom-none/proxy", "dom-none/rendered", "api-boundary/direct", "api-boundary/proxy", "api-boundary/rendered", "dom-group/direct", "dom-group/proxy", "dom-group/rendered"}, firstSharedDOMAPIFixture)
}
func TestRealSharedDOMAPIVariantColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api-auto/direct", "api-auto/proxy", "api-auto/rendered", "dom-jsonld/direct", "dom-jsonld/proxy", "dom-jsonld/rendered", "dom-include/direct", "dom-include/proxy", "dom-include/rendered"}, firstSharedDOMAPIFixture)
}
