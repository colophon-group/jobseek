package queue

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"testing"

	"github.com/jackc/pgx/v5"
)

func TestOriginalUnisanteMigrationReceipts(t *testing.T) {
	b, err := os.ReadFile("../api-sniffer-monitor/testdata/python_unisante_migration_receipts.json")
	var cases []struct {
		Name    string
		Receipt json.RawMessage
		Matches bool
	}
	if err != nil || json.Unmarshal(b, &cases) != nil || len(cases) != 42 {
		t.Fatal("original receipt corpus unavailable")
	}
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			if validUnisanteMigrationReceipt(c.Receipt) != c.Matches {
				t.Fatal("original receipt decision changed")
			}
		})
	}
}

func TestRealUnisanteOriginalAdoptionAndRollback(t *testing.T) {
	for _, mode := range []string{"commit", "rollback", "unknown", "foreign", "bad-receipt", "wrong-owner", "over-cap"} {
		t.Run(mode, func(t *testing.T) {
			f := realAuthority(t, Monitor, Simple)
			ctx := context.Background()
			config := map[string]string{"board_slug": "unisante-emploi", "board_url": "https://emploi.unisante.ch/index.php/offres", "crawler_type": "unisante", "metadata": `{"identity_migration":"unisante-provider-reference-v1","scraper_type":"skip"}`}
			canonical := "https://emploi.unisante.ch/index.php/offre/42-clinical-role"
			alias, expired := ordinaryID(t), ordinaryID(t)
			if _, err := f.observer.Exec(ctx, "UPDATE company SET slug='unisante' WHERE id=$1::uuid", f.company); err != nil {
				t.Fatal(err)
			}
			if _, err := f.observer.Exec(ctx, "UPDATE job_board SET board_slug=$2,board_url=$3,crawler_type='unisante',metadata=$4::jsonb WHERE id=$1::uuid", f.task.ID, config["board_slug"], config["board_url"], config["metadata"]); err != nil {
				t.Fatal(err)
			}
			if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_url=$2,source_identity=$2 WHERE id=$1::uuid", f.task.ID, canonical); err != nil {
				t.Fatal(err)
			}
			for id, source := range map[string]string{alias: "https://emploi.unisante.ch/offre/42-old-title", expired: "https://emploi.unisante.ch/index.php/offre/99-expired-role"} {
				if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,source_identity,titles,locales,next_scrape_at) VALUES($1::uuid,$2::uuid,$3::uuid,$4,$4,ARRAY['Legacy'],ARRAY['fr'],clock_timestamp())`, id, f.company, f.task.ID, source); err != nil {
					t.Fatal(err)
				}
			}
			t.Cleanup(func() {
				_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=ANY($1::uuid[])", []string{alias, expired})
			})
			if mode == "unknown" {
				if _, err := f.observer.Exec(ctx, "UPDATE job_posting SET source_identity='unknown' WHERE id=$1::uuid", expired); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "wrong-owner" {
				if _, err := f.observer.Exec(ctx, "UPDATE company SET slug=$2 WHERE id=$1::uuid", f.company, "wrong-"+f.company); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "over-cap" {
				extra := []string{}
				for index := 0; index < 48; index++ {
					id := ordinaryID(t)
					extra = append(extra, id)
					source := fmt.Sprintf("https://emploi.unisante.ch/index.php/offre/%d-legacy-role", 1000+index)
					if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,board_id,source_url,source_identity,titles,locales) VALUES($1::uuid,$2::uuid,$3::uuid,$4,$4,ARRAY['Legacy'],ARRAY['fr'])`, id, f.company, f.task.ID, source); err != nil {
						t.Fatal(err)
					}
				}
				t.Cleanup(func() {
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=ANY($1::uuid[])", extra)
				})
			}
			if mode == "bad-receipt" {
				if _, err := f.observer.Exec(ctx, `UPDATE job_board SET metadata=metadata || '{"_identity_migration_receipt":{"id":"wrong"}}'::jsonb WHERE id=$1::uuid`, f.task.ID); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "foreign" {
				other := ordinaryID(t)
				foreign := ordinaryID(t)
				if _, err := f.observer.Exec(ctx, "INSERT INTO company(id,slug,name) VALUES($1::uuid,$2,'Foreign synthetic owner')", other, "other-"+other); err != nil {
					t.Fatal(err)
				}
				if _, err := f.observer.Exec(ctx, `INSERT INTO job_posting(id,company_id,source_url,source_identity,titles,locales) VALUES($1::uuid,$2::uuid,'https://foreign.invalid/job','unisante:emploi:42',ARRAY['Foreign'],ARRAY['en'])`, foreign, other); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() {
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM job_posting WHERE id=$1::uuid", foreign)
					_, _ = f.observer.Exec(context.Background(), "DELETE FROM company WHERE id=$1::uuid", other)
				})
			}
			tx, err := f.observer.Begin(ctx)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback(ctx)
			retired, err := migrateUnisanteProviderIdentities(ctx, tx, f.task.ID, f.company, config, []string{"unisante:emploi:42"}, []string{canonical})
			blocked := mode != "commit" && mode != "rollback"
			if blocked {
				if !errors.Is(err, ErrConfiguration) {
					t.Fatal("original safety gate did not block", err)
				}
				if err := tx.Rollback(ctx); err != nil {
					t.Fatal(err)
				}
			} else {
				if err != nil || retired != 2 {
					t.Fatal("original alias adoption changed", err, retired)
				}
				if mode == "rollback" {
					if err := tx.Rollback(ctx); err != nil {
						t.Fatal(err)
					}
				} else {
					if err := tx.Commit(ctx); err != nil {
						t.Fatal(err)
					}
				}
			}
			var identity string
			var active bool
			var count int
			var receipt json.RawMessage
			if err := f.observer.QueryRow(ctx, "SELECT source_identity,is_active FROM job_posting WHERE id=$1::uuid", f.task.ID).Scan(&identity, &active); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT count(*) FROM job_posting WHERE id=ANY($1::uuid[]) AND is_active", []string{alias, expired}).Scan(&count); err != nil {
				t.Fatal(err)
			}
			if err := f.observer.QueryRow(ctx, "SELECT metadata->'_identity_migration_receipt' FROM job_board WHERE id=$1::uuid", f.task.ID).Scan(&receipt); err != nil {
				t.Fatal(err)
			}
			if mode == "commit" {
				if identity != "unisante:emploi:42" || !active || count != 0 || !validUnisanteMigrationReceipt(receipt) {
					t.Fatal("atomic adoption/retirement/receipt changed")
				}
				if err := pgx.BeginFunc(ctx, f.observer, func(tx pgx.Tx) error {
					n, e := migrateUnisanteProviderIdentities(ctx, tx, f.task.ID, f.company, config, []string{"unisante:emploi:42"}, []string{canonical})
					if n != 0 {
						t.Fatal("applied receipt repeated adoption")
					}
					return e
				}); err != nil {
					t.Fatal("valid receipt reuse failed", err)
				}
			} else if identity != canonical || !active || count != 2 || mode != "bad-receipt" && len(receipt) > 0 {
				t.Fatal("failed or interrupted migration changed canonical state")
			}
		})
	}
}
