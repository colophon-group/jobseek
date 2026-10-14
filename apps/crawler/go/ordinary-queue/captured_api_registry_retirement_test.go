package queue

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func capturedAPIRegistryConfig(t *testing.T, slug string) map[string]string {
	t.Helper()
	f, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	rows, err := csv.NewReader(f).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	columns := map[string]int{}
	for i, key := range rows[0] {
		columns[key] = i
	}
	for _, row := range rows[1:] {
		if row[columns["board_slug"]] != slug {
			continue
		}
		var md map[string]any
		if json.Unmarshal([]byte(row[columns["monitor_config"]]), &md) != nil {
			t.Fatal("invalid registry metadata")
		}
		md["scraper_type"] = row[columns["scraper_type"]]
		if raw := row[columns["scraper_config"]]; raw != "" {
			var detail any
			if json.Unmarshal([]byte(raw), &detail) != nil {
				t.Fatal("invalid detail configuration")
			}
			md["scraper_config"] = detail
		}
		raw, _ := json.Marshal(md)
		config := profileConfig()
		config["board_url"], config["crawler_type"], config["metadata"] = row[columns["board_url"]], row[columns["monitor_type"]], string(raw)
		config["monitor_needs_browser"] = "0"
		if slug == "bp-travelcenters" {
			config["monitor_needs_browser"] = "1"
		}
		p, err := InspectRichMonitor("11111111-1111-1111-1111-111111111111", config)
		want := "api_sniffer.http-items/v1"
		if slug == "bp-travelcenters" {
			want = "api_sniffer.browser-items/v1"
		}
		if err != nil || p.Profile != want {
			t.Fatal("published transport not admitted", slug, p.Profile, err)
		}
		skip, err := MonitorSkipsSSL(config)
		if err != nil || skip != (slug == "didi-global-careers-intl") {
			t.Fatal("original explicit TLS choice lost", slug, err)
		}
		if slug == "bp-travelcenters" && md["settle"] != float64(10) {
			t.Fatal("qualified session wait lost")
		}
		return config
	}
	t.Fatal("published candidate missing", slug)
	return nil
}

func TestCapturedAPIRegistrySelectsQualifiedTransports(t *testing.T) {
	for _, slug := range []string{"airbnb-contractors", "didi-global-careers-intl", "bp-travelcenters"} {
		t.Run(slug, func(t *testing.T) { capturedAPIRegistryConfig(t, slug) })
	}
}

func capturedAPIColdFixture(t *testing.T, variant string) firstOwnerFixture {
	slug := strings.TrimPrefix(variant, "api_sniffer/")
	c := capturedAPIRegistryConfig(t, slug)
	var p firstOwnerFixture
	if slug == "bp-travelcenters" {
		p = firstRenderedMonitorFixture(t)
	} else {
		p = firstProviderBatchFixture(t, "api_sniffer")
	}
	f, ctx := p.f, context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET crawler_type='api_sniffer',board_url=$2,metadata=$3::jsonb WHERE id=$1::uuid", f.task.ID, c["board_url"], c["metadata"]); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "crawler_type", "api_sniffer", "board_url", c["board_url"], "metadata", c["metadata"]).Err(); err != nil {
		t.Fatal(err)
	}
	plan, err := f.authority.StageOwnership(ctx, p.plan.SourceRevision(), []string{f.task.ID}, nil)
	if err != nil {
		t.Fatal(err)
	}
	p.plan = plan
	t.Cleanup(func() {
		_, _ = f.observer.Exec(context.Background(), "UPDATE ordinary_worker_ownership_plan SET state='retired' WHERE plan_sha256=$1 AND state='active'", plan.SHA256())
	})
	return p
}

func TestRealCapturedAPIRegistryColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api_sniffer/airbnb-contractors", "api_sniffer/didi-global-careers-intl", "api_sniffer/bp-travelcenters"}, capturedAPIColdFixture)
}
