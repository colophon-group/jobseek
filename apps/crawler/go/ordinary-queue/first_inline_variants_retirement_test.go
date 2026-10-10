package queue

import (
	"context"
	"encoding/json"
	"testing"
)

func firstInlineVariantFixture(t *testing.T, variant string) firstOwnerFixture {
	provider := "dom"
	if variant == "rss-description" {
		provider = "rss"
	}
	if variant == "api-legacy-literal" {
		provider = "api_sniffer"
	}
	p := firstProviderBatchFixture(t, provider)
	ctx := context.Background()
	var raw []byte
	if err := p.f.observer.QueryRow(ctx, "SELECT metadata FROM job_board WHERE id=$1::uuid", p.f.task.ID).Scan(&raw); err != nil {
		t.Fatal(err)
	}
	md := map[string]any{}
	if json.Unmarshal(raw, &md) != nil {
		t.Fatal("metadata")
	}
	if provider == "dom" {
		md = map[string]any{"scraper_type": "json-ld"}
		if variant == "onclick" {
			md["onclick_selector"] = "tr.item"
		} else {
			script := map[string]any{"variable": "jobs", "url_field": "link", "url_template": "{value}"}
			if variant == "script-rich" {
				script["title_field"] = "title"
				script["locations_field"] = "locations"
				md["scraper_config"] = map[string]any{"enrich": []string{"description"}}
			}
			md["script_json_links"] = script
		}
	}
	if provider == "rss" {
		md["scraper_type"] = "dom"
		md["scraper_config"] = map[string]any{"enrich": []string{"description"}, "steps": []any{map[string]any{"tag": "p", "field": "description", "html": true}}}
	}
	if provider == "api_sniffer" {
		md["json_path"] = "jobs[?contains(title, `Geneva`)]"
	}
	raw, _ = json.Marshal(md)
	if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", p.f.task.ID, string(raw)); err != nil {
		t.Fatal(err)
	}
	if err := p.f.client.redis.HSet(ctx, "board:"+p.f.task.ID, "metadata", string(raw)).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := p.f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{p.f.task.ID}, nil)
	if err != nil {
		t.Fatal("variant cold stage", err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = p.f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}
func TestRealStaticInlineAPIRSSVariantsColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"onclick", "script-url", "script-rich", "rss-description", "api-legacy-literal"}, firstInlineVariantFixture)
}
