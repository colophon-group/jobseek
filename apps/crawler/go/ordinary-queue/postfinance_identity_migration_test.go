package queue

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestPostfinanceMigrationProfileAndReceiptBindings(t *testing.T) {
	c := profileConfig()
	c["crawler_type"], c["board_slug"], c["board_url"] = "rss", "postfinance-careers", postfinanceMigrationBoardURL
	c["metadata"] = `{"preset":"successfactors","feed_url":"https://jobs.postfinance.ch/googlefeed.xml","scraper_type":"skip","identity_migration":"postfinance-swiss-post-stable-id-v1"}`
	p, err := InspectRichMonitor(profileBoardID, c)
	if err != nil {
		t.Fatal("code-owned RSS migration cannot be inspected", err)
	}
	var md map[string]any
	if json.Unmarshal([]byte(c["metadata"]), &md) != nil {
		t.Fatal("fixture metadata")
	}
	receipt := map[string]any{"id": postfinanceIdentityMigration, "version": 1, "config_fingerprint": postfinanceMigrationFingerprint, "completed_at": "fixture", "retired_count": 2}
	raw, _ := json.Marshal(receipt)
	if !validPostfinanceMigrationReceipt(raw) {
		t.Fatal("exact receipt rejected")
	}
	md["_identity_migration_receipt"] = receipt
	raw, _ = json.Marshal(md)
	c["metadata"] = string(raw)
	replayed, err := InspectRichMonitor(profileBoardID, c)
	if err != nil || replayed.EffectiveConfigSHA256 != p.EffectiveConfigSHA256 {
		t.Fatal("receipt invalidated stable migration configuration", err)
	}
	for _, key := range []string{"board_slug", "board_url", "crawler_type"} {
		wrong := cloneConfig(c)
		wrong[key] = "wrong"
		if _, err := InspectRichMonitor(profileBoardID, wrong); err == nil {
			t.Fatal("migration admitted outside code-owned contract", key)
		}
	}
	for _, value := range []any{-1, 2001, true, "2", nil} {
		receipt["retired_count"] = value
		raw, _ := json.Marshal(receipt)
		if validPostfinanceMigrationReceipt(raw) {
			t.Fatal("invalid receipt count admitted")
		}
	}
}

func TestRealPostfinanceOriginalRetirementAndSafetyGates(t *testing.T) {
	for _, mode := range []string{"commit", "rollback", "unknown", "wrong-owner", "stale", "partial", "filtered", "history", "drop", "fingerprint", "bad-receipt", "over-cap", "noncanonical"} {
		t.Run(mode, func(t *testing.T) {
			f := realAuthority(t, Monitor, Simple)
			ctx := context.Background()
			canonical := "https://jobs.postfinance.ch/job/_/42/"
			alias, expired := ordinaryID(t), ordinaryID(t)
			if _, err := f.observer.Exec(ctx, "UPDATE company SET slug='postfinance' WHERE id=$1::uuid", f.company); err != nil {
				t.Fatal(err)
			}
			metadata := `{"identity_migration":"postfinance-swiss-post-stable-id-v1","_monitor_config_fingerprint":"` + postfinanceMigrationFingerprint + `","recent_discovered_counts":[1,1,1]}`
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb WHERE id=$1::uuid", f.task.ID, metadata); err != nil {
				t.Fatal(err)
			}
			if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2,source_identity=$2,last_seen_at=clock_timestamp() WHERE id=$1::uuid", f.task.ID, canonical); err != nil {
				t.Fatal(err)
			}
			for id, source := range map[string]string{alias: "https://job.post.ch/PostFinance/job/old/42-de_DE", expired: "https://career.post.ch/de"} {
				// NULL board_id models legacy identities left by removed boards.
				if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,source_url,source_identity,titles,locales,next_scrape_at) VALUES($1::uuid,$2::uuid,$3,$3,ARRAY['Legacy'],ARRAY['de'],clock_timestamp())`, id, f.company, source); err != nil {
					t.Fatal(err)
				}
			}
			t.Cleanup(func() {
				_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE company_id=$1::uuid AND id<>$2::uuid", f.company, f.task.ID)
			})
			var md map[string]any
			decoder := json.NewDecoder(strings.NewReader(metadata))
			decoder.UseNumber()
			if decoder.Decode(&md) != nil {
				t.Fatal("fixture metadata")
			}
			cycle := &GreenhouseCycle{claim: &Claim{task: Task{ID: f.task.ID, Config: map[string]string{"company_id": f.company, "crawler_type": "rss", "board_slug": "postfinance-careers", "board_url": postfinanceMigrationBoardURL}}}, startedAt: time.Now().Add(-time.Second), processed: 1, identities: map[string]bool{canonical: true}}
			summary := GreenhouseInventorySummary{Discovered: 1}
			switch mode {
			case "unknown":
				_, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url='https://unknown.invalid/job' WHERE id=$1::uuid", expired)
				if err != nil {
					t.Fatal(err)
				}
			case "wrong-owner":
				_, err := f.observer.Exec(ctx, "UPDATE company SET slug=$2 WHERE id=$1::uuid", f.company, "wrong-"+f.company)
				if err != nil {
					t.Fatal(err)
				}
			case "stale":
				cycle.startedAt = time.Now().Add(time.Hour)
			case "partial":
				summary.Truncated = true
			case "filtered":
				summary.Discovered, summary.ProcessingFiltered = 2, 1
			case "history":
				md["recent_discovered_counts"] = []any{json.Number("1")}
			case "drop":
				md["recent_discovered_counts"] = []any{json.Number("10"), json.Number("10"), json.Number("10")}
			case "fingerprint":
				md["_monitor_config_fingerprint"] = "changed"
			case "bad-receipt":
				md["_identity_migration_receipt"] = map[string]any{"id": "wrong"}
			case "over-cap":
				_, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,source_url,titles,locales) SELECT gen_random_uuid(),$1::uuid,'https://job.post.ch/PostFinance/job/legacy/' || n || '-de_DE',ARRAY['Legacy'],ARRAY['de'] FROM generate_series(10000,11998) n`, f.company)
				if err != nil {
					t.Fatal(err)
				}
			case "noncanonical":
				cycle.identities = map[string]bool{"https://unknown.invalid/job": true}
			}
			tx, err := f.observer.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			retired, err := cycle.migratePostfinanceIdentities(ctx, tx, md, summary)
			want := 0
			if mode == "commit" || mode == "rollback" {
				want = 2
			}
			if err != nil || retired != want {
				t.Fatal("original retirement decision changed", mode, retired, err)
			}
			if mode == "commit" {
				if err := tx.Commit(ctx); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := tx.Rollback(ctx); err != nil {
					t.Fatal(err)
				}
			}
			var count int
			var receipt json.RawMessage
			if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE id=ANY($1::uuid[]) AND is_active", []string{alias, expired}).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&receipt); err != nil {
				t.Fatal(err)
			}
			if mode == "commit" {
				if count != 0 || !validPostfinanceMigrationReceipt(receipt) {
					t.Fatal("atomic retirement/receipt changed")
				}
				replay, err := f.observer.Begin(ctx)
				if err != nil {
					t.Fatal(err)
				}
				defer replay.Rollback(ctx)
				n, err := cycle.migratePostfinanceIdentities(ctx, replay, md, summary)
				if err != nil || n != 0 {
					t.Fatal("receipt did not make replay inert", err, n)
				}
			} else if count != 2 || len(receipt) > 0 {
				t.Fatal("blocked or rolled back retirement changed rows")
			}
		})
	}
}
