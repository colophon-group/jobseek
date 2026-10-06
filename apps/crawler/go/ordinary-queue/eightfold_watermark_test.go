package queue

import (
	"context"
	"encoding/json"
	"reflect"
	"testing"
)

func providerBatchWatermark() map[string]any {
	return map[string]any{"max_ts": json.Number("123"), "interval_days": json.Number("7"), "auto_full_crawl": true, "enabled": true, "last_full_at": "2026-10-06T03:00:00.120000+00:00", "last_incremental_at": "2026-10-06T03:00:00.120000+00:00", "extra": map[string]any{"host": "example.com", "domain": "fixture"}}
}

func TestEightfoldTerminalWatermarkRejectsRollbackAndForeignConfiguration(t *testing.T) {
	for _, mode := range []string{"valid", "foreign-provider", "extra-root", "max-rollback", "full-time-rollback", "incremental-time-rollback", "bad-time", "changed-interval", "changed-auto", "foreign-host", "missing-domain", "unknown-field", "null-enabled", "null-extra"} {
		t.Run(mode, func(t *testing.T) {
			current := map[string]any{"pcsx_watermark": providerBatchWatermark()}
			next := providerBatchWatermark()
			config := map[string]string{"crawler_type": "eightfold", "board_url": "https://example.com/careers"}
			patch := map[string]any{"pcsx_watermark": next}
			switch mode {
			case "foreign-provider":
				config["crawler_type"] = "mokahr"
			case "extra-root":
				patch["scraper_type"] = "skip"
			case "max-rollback":
				next["max_ts"] = 122
			case "full-time-rollback":
				next["last_full_at"] = "2026-10-05T03:00:00+00:00"
			case "incremental-time-rollback":
				next["last_incremental_at"] = "2026-10-05T03:00:00+00:00"
			case "bad-time":
				next["last_full_at"] = "bad"
			case "changed-interval":
				next["interval_days"] = 3
			case "changed-auto":
				next["auto_full_crawl"] = false
			case "foreign-host":
				next["extra"].(map[string]any)["host"] = "other.example.com"
			case "missing-domain":
				delete(next["extra"].(map[string]any), "domain")
			case "unknown-field":
				next["other"] = true
			case "null-enabled":
				next["enabled"] = nil
			case "null-extra":
				next["extra"] = nil
			}
			err := validateEightfoldWatermarkUpdate(config, current, patch)
			if (err == nil) != (mode == "valid") {
				t.Fatal("watermark mutation boundary differs", mode, err)
			}
		})
	}
}

func TestRealEightfoldMetadataSettlementPreflightsBeforeQueueOrCacheMutation(t *testing.T) {
	for _, mode := range []string{"valid", "stale-cache", "malformed-metadata", "corrupt-token-index", "cancelled", "stale-token"} {
		t.Run(mode, func(t *testing.T) {
			p := firstProviderBatchFixture(t, "eightfold")
			_, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			metadata := map[string]any{"scraper_type": "eightfold", "pcsx_watermark": providerBatchWatermark()}
			raw, err := json.Marshal(metadata)
			if err != nil {
				t.Fatal(err)
			}
			next := string(raw)
			switch mode {
			case "stale-cache":
				if err := p.f.client.redis.HSet(ctx, "board:"+claim.task.ID, "metadata", `{"scraper_type":"eightfold","operator_setting":true}`).Err(); err != nil {
					t.Fatal(err)
				}
			case "malformed-metadata":
				next = "{bad"
			case "corrupt-token-index":
				if err := p.f.client.redis.Del(ctx, "inflight_tokens:simple").Err(); err != nil {
					t.Fatal(err)
				}
				if err := p.f.client.redis.Set(ctx, "inflight_tokens:simple", "corrupt", 0).Err(); err != nil {
					t.Fatal(err)
				}
			case "cancelled":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			case "stale-token":
				claim.task.claimToken = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
			}
			before := snapshot(t, p.f.client)
			ok, err := p.f.client.rescheduleMetadata(ctx, &claim.task, claim.task.InitialLeaseUntil+3600, nil, &next)
			if mode == "valid" {
				if err != nil || !ok || p.f.client.redis.HGet(context.Background(), "board:"+claim.task.ID, "metadata").Val() != next || p.f.client.redis.ZCard(context.Background(), "inflight:simple").Val() != 0 {
					t.Fatal("atomic metadata settlement failed", err)
				}
			} else if ok || !reflect.DeepEqual(before, snapshot(t, p.f.client)) {
				t.Fatal("refused settlement changed queue/cache", mode, err)
			}
		})
	}
}

func TestRealEightfoldWatermarkTerminalRefusalRollsBackCanonicalAndReceipt(t *testing.T) {
	for _, mode := range []string{"truncated", "invalid-inventory", "cancelled", "changed-config", "foreign-host"} {
		t.Run(mode, func(t *testing.T) {
			p := firstProviderBatchFixture(t, "eightfold")
			a, claim := firstRetirementClaim(t, p)
			ctx := context.Background()
			cycle, err := a.BeginGreenhouseCycle(ctx, claim)
			if err != nil {
				t.Fatal(err)
			}
			summary := GreenhouseInventorySummary{MetadataUpdates: map[string]any{"pcsx_watermark": providerBatchWatermark()}}
			switch mode {
			case "truncated":
				summary.Truncated = true
			case "invalid-inventory":
				summary.Discovered = 1
			case "cancelled":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			case "changed-config":
				if _, err := p.f.observer.Exec(ctx, "UPDATE job_board SET check_interval_minutes=120 WHERE id=$1::uuid", claim.task.ID); err != nil {
					t.Fatal(err)
				}
			case "foreign-host":
				summary.MetadataUpdates["pcsx_watermark"].(map[string]any)["extra"].(map[string]any)["host"] = "other.example.com"
			}
			before := coldCanonicalSnapshot(t, p.f)
			cached := snapshot(t, p.f.client)
			// Authority verification may renew the current lease, even when the
			// posting transaction is refused. Queue membership/tokens stay intact.
			delete(cached, "inflight:simple")
			if _, err := cycle.FinishSuccess(ctx, summary); err == nil {
				t.Fatal("invalid terminal inventory committed", mode)
			}
			after := snapshot(t, p.f.client)
			delete(after, "inflight:simple")
			if before != coldCanonicalSnapshot(t, p.f) || !reflect.DeepEqual(cached, after) || p.f.client.redis.ZCard(context.Background(), "inflight:simple").Val() != 1 {
				t.Fatal("rejected terminal wrote watermark/receipt/queues", mode)
			}
		})
	}
}
