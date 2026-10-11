package queue

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"os"
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
	boardURL := "https://example.com/careers"
	if kind == "dom-webforms" {
		boardURL = "https://careers.slaughterandmay.com/VacanciesV2.aspx"
		f, err := os.Open("../../data/boards.csv")
		if err != nil {
			t.Fatal(err)
		}
		rows, err := csv.NewReader(f).ReadAll()
		f.Close()
		if err != nil || len(rows) < 2 {
			t.Fatal("canonical form fixture unavailable", err)
		}
		keys := map[string]int{}
		for i, k := range rows[0] {
			keys[k] = i
		}
		found := false
		for _, row := range rows[1:] {
			if row[keys["board_slug"]] == "slaughter-and-may-careers" {
				if json.Unmarshal([]byte(row[keys["monitor_config"]]), &md) != nil {
					t.Fatal("canonical form actions unavailable")
				}
				found = true
			}
		}
		if !found {
			t.Fatal("canonical form fixture missing")
		}
	}
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
	if kind == "dom-retry-hint" {
		md["retry_statuses"] = map[string]int{"503": 2}
	}
	if kind == "api-pagination-browser-hint" {
		md["pagination"] = map[string]any{"param_name": "page", "start_value": 1, "browser": true}
	}
	if kind == "api-root" {
		md["json_path"] = "$"
	}
	if kind == "api-decrypt" || kind == "api-decrypt-fixed" {
		md["json_path"] = "Data.jobs"
		decrypt := map[string]any{"key": "fixture-key-1234"}
		if kind == "api-decrypt-fixed" {
			decrypt["iv_mode"] = "fixed:0123456789abcdef"
		}
		md["response_decrypt"] = decrypt
	}
	if kind == "api-refresh" {
		md["method"], md["post_data"] = "POST", "nonce=old&page=1"
		md["post_data_refresh"] = map[string]any{"fields": map[string]any{"nonce": "nonce=([a-z]+)"}}
	}
	if kind == "dom-euc-jp" {
		md["encoding"] = "euc_jp"
	}
	if kind == "dom-provider" {
		md["lg_portal"] = true
	}
	if kind == "dom-rich-empty" || kind == "dom-prospective" {
		md["rich_rows"] = map[string]any{"row_selector": ".job", "link_selector": "a[href]"}
		md["empty_selector"], md["empty_text"] = ".empty", "No jobs"
	}
	if kind == "dom-prospective" {
		delete(md, "empty_selector")
		delete(md, "empty_text")
		md["prospective_board"], md["prospective_canonical_path"] = "1000973", "/offene-stellen/job/"
		md["rich_rows"] = map[string]any{"row_selector": "#jobs-list .job", "link_selector": "a[href]", "total_selector": ".total"}
		md["empty_states"] = []any{map[string]any{"selector": "body.career-center:has(#jobs-list) .total", "exact_text": "0"}}
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
	if _, e := p.f.observer.Exec(ctx, "UPDATE job_board SET crawler_type=$2,board_url=$4,metadata=$3::jsonb WHERE id=$1::uuid", p.f.task.ID, provider, string(raw), boardURL); e != nil {
		t.Fatal(e)
	}
	if e := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "crawler_type", provider, "board_url", boardURL, "metadata", string(raw)).Err(); e != nil {
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

func TestRealDOMProviderAndAPIRootColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"dom-provider/direct", "dom-provider/proxy", "dom-provider/rendered", "dom-rich-empty/direct", "dom-rich-empty/proxy", "dom-rich-empty/rendered", "dom-prospective/direct", "dom-prospective/proxy", "api-root/direct", "api-root/proxy", "api-root/rendered"}, firstSharedDOMAPIFixture)
}

func TestRealEncryptedInitialAPIVariantColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api-decrypt/direct", "api-decrypt-fixed/direct"}, firstSharedDOMAPIFixture)
}

func TestRealHTTPTokenRefreshAndJapaneseEncodingColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api-refresh/direct", "api-refresh/proxy", "dom-euc-jp/direct", "dom-euc-jp/proxy"}, firstSharedDOMAPIFixture)
}

func TestRealPublishedWebFormsColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"dom-webforms/rendered"}, firstSharedDOMAPIFixture)
}

func TestRealLegacyMonitorHintsColdRetirementConservesQueues(t *testing.T) {
	testProviderColdRetirement(t, []string{"dom-retry-hint/direct", "api-pagination-browser-hint/direct"}, firstSharedDOMAPIFixture)
}
