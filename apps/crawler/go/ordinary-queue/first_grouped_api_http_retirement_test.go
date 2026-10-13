package queue

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"os"
	"strings"
	"testing"
)

func groupedAPIHTTPRegistryConfig(t *testing.T, slug string) map[string]string {
	t.Helper()
	file, err := os.Open("../../data/boards.csv")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	rows, err := csv.NewReader(file).ReadAll()
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
		var metadata map[string]any
		if err := json.Unmarshal([]byte(row[columns["monitor_config"]]), &metadata); err != nil {
			t.Fatal(err)
		}
		for _, key := range []string{"browser", "stealth", "proxy", "route_params", "disable_http2", "api_url_match"} {
			if _, ok := metadata[key]; ok {
				t.Fatal("published HTTP replacement retains browser transport control", key)
			}
		}
		metadata["scraper_type"] = row[columns["scraper_type"]]
		body, err := json.Marshal(metadata)
		if err != nil {
			t.Fatal(err)
		}
		config := profileConfig()
		config["board_url"], config["crawler_type"], config["metadata"] = row[columns["board_url"]], row[columns["monitor_type"]], string(body)
		config["monitor_needs_browser"] = "0"
		profile, err := InspectRichMonitor(strings.Repeat("1", 8)+"-1111-1111-1111-111111111111", config)
		if err != nil || profile.Profile != "api_sniffer.http-items/v1" || profile.Endpoint == "" {
			t.Fatal("published replacement does not select native HTTP", slug, err)
		}
		return config
	}
	t.Fatal("published replacement missing", slug)
	return nil
}

func TestGroupedAPIHTTPRegistrySelectsNativeRoutes(t *testing.T) {
	for _, slug := range []string{"sage-careers-search", "mckinsey-careers"} {
		groupedAPIHTTPRegistryConfig(t, slug)
	}
}

func groupedAPIHTTPColdFixture(t *testing.T, variant string) firstOwnerFixture {
	p := firstProviderBatchFixture(t, "api_sniffer")
	config := groupedAPIHTTPRegistryConfig(t, strings.TrimPrefix(variant, "api_sniffer/"))
	f, ctx := p.f, context.Background()
	if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_url=$2,metadata=$3::jsonb WHERE id=$1::uuid", f.task.ID, config["board_url"], config["metadata"]); err != nil {
		t.Fatal(err)
	}
	if err := f.client.redis.HSet(ctx, "board:"+f.task.ID, "board_url", config["board_url"], "metadata", config["metadata"]).Err(); err != nil {
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

func TestRealGroupedAPIHTTPColdRetirement(t *testing.T) {
	testProviderColdRetirement(t, []string{"api_sniffer/sage-careers-search", "api_sniffer/mckinsey-careers"}, groupedAPIHTTPColdFixture)
}
