package main

import (
	"context"
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/jackc/pgx/v5"
	"os"
	"strings"
	"time"
)

//go:embed local-migrations/*.sql local-migrations/manifest.json
var localMigrationFiles embed.FS

type localMigration struct {
	ID, Previous, File, SHA256, Special string
	Statements                          []string
	StatementsSHA256                    string `json:"statements_sha256"`
	RepairSHA256                        string `json:"repair_sha256"`
	Chunk                               int
	MaxIterations                       int    `json:"max_iterations"`
	IndexName                           string `json:"index_name"`
}
type localMigrationManifest struct {
	Schema, Head string
	Revisions    []localMigration
	SourceHashes map[string]string `json:"source_hashes"`
}

func loadLocalMigrations() (localMigrationManifest, error) {
	var manifest localMigrationManifest
	body, err := localMigrationFiles.ReadFile("local-migrations/manifest.json")
	if err != nil || json.Unmarshal(body, &manifest) != nil || manifest.Schema != "jobseek.local-schema-migrations/v1" || len(manifest.Revisions) == 0 {
		return manifest, errors.New("local migration manifest invalid")
	}
	previous := ""
	for _, revision := range manifest.Revisions {
		if len(revision.ID) != 4 || revision.ID < "0001" || revision.Previous != previous || revision.File != revision.ID+".sql" || revision.Special != "" && revision.Special != "phantom-chunks" && revision.Special != "location-name-index" {
			return manifest, errors.New("local migration chain invalid")
		}
		body, err := localMigrationFiles.ReadFile("local-migrations/" + revision.File)
		digest := sha256.Sum256(body)
		if err != nil || hex.EncodeToString(digest[:]) != revision.SHA256 {
			return manifest, errors.New("local migration SQL identity invalid")
		}
		statements := sha256.Sum256([]byte(strings.Join(revision.Statements, "\x00")))
		if len(revision.Statements) == 0 || hex.EncodeToString(statements[:]) != revision.StatementsSHA256 {
			return manifest, errors.New("local migration statement identity invalid")
		}
		if revision.Special == "phantom-chunks" {
			body, err := localMigrationFiles.ReadFile("local-migrations/phantom-chunks.sql")
			sum := sha256.Sum256(body)
			if err != nil || hex.EncodeToString(sum[:]) != revision.RepairSHA256 {
				return manifest, errors.New("local repair statement identity invalid")
			}
		}
		previous = revision.ID
	}
	if previous != manifest.Head {
		return manifest, errors.New("local migration head invalid")
	}
	return manifest, nil
}

func currentLocalMigration(ctx context.Context, conn *pgx.Conn) (string, error) {
	var relation *string
	if err := conn.QueryRow(ctx, "SELECT to_regclass('public.alembic_version')::text").Scan(&relation); err != nil {
		return "", err
	}
	if relation == nil {
		return "", nil
	}
	rows, err := conn.Query(ctx, "SELECT version_num FROM public.alembic_version")
	if err != nil {
		return "", err
	}
	defer rows.Close()
	current := ""
	for rows.Next() {
		if current != "" {
			return "", errors.New("multiple local migration heads")
		}
		if err := rows.Scan(&current); err != nil {
			return "", err
		}
	}
	return current, rows.Err()
}

// Match Alembic's transactional DDL and emitted autocommit transitions.
// An interrupted special operation leaves the previous revision
// stamped so the original bounded repair/retry runs again.
func migrateLocal(ctx context.Context, conn *pgx.Conn) (string, error) {
	return migrateLocalThrough(ctx, conn, "")
}

func migrateLocalThrough(ctx context.Context, conn *pgx.Conn, target string) (string, error) {
	manifest, err := loadLocalMigrations()
	if err != nil {
		return "", err
	}
	if target != "" {
		found := false
		for i, revision := range manifest.Revisions {
			if revision.ID == target {
				manifest.Revisions = manifest.Revisions[:i+1]
				manifest.Head = target
				found = true
				break
			}
		}
		if !found {
			return "", errors.New("unsupported local migration target")
		}
	}
	current, err := currentLocalMigration(ctx, conn)
	if err != nil {
		return "", errors.New("local schema revision unavailable")
	}
	start := 0
	if current != "" {
		start = -1
		for i, r := range manifest.Revisions {
			if r.ID == current {
				start = i + 1
				break
			}
		}
		if start < 0 {
			return "", errors.New("unsupported local schema revision")
		}
	}
	var tx pgx.Tx
	defer func() {
		if tx != nil {
			rollback, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			_ = tx.Rollback(rollback)
		}
	}()
	commit := func() error {
		if tx == nil {
			return nil
		}
		err := tx.Commit(ctx)
		tx = nil
		return err
	}
	for _, revision := range manifest.Revisions[start:] {
		if revision.Special != "" {
			if revision.Special == "phantom-chunks" {
				if err := commit(); err != nil {
					return "", fmt.Errorf("local migration %s commit rejected", revision.ID)
				}
				query, err := localMigrationFiles.ReadFile("local-migrations/phantom-chunks.sql")
				if err != nil {
					return "", err
				}
				if revision.Chunk != 1000 || revision.MaxIterations != 500 {
					return "", errors.New("phantom migration bounds invalid")
				}
				for i := 0; i < revision.MaxIterations; i++ {
					tag, err := conn.Exec(ctx, string(query), revision.Chunk)
					if err != nil {
						return "", fmt.Errorf("local migration %s repair rejected", revision.ID)
					}
					if tag.RowsAffected() == 0 {
						break
					}
				}
			} else {
				var table *string
				query := conn.QueryRow
				if tx != nil {
					query = tx.QueryRow
				}
				if err := query(ctx, "SELECT to_regclass('public.location_name')::text").Scan(&table); err != nil {
					return "", errors.New("local location index preflight rejected")
				}
				if table != nil {
					if err := commit(); err != nil {
						return "", fmt.Errorf("local migration %s commit rejected", revision.ID)
					}
					if revision.IndexName != "idx_location_name_lower_lookup" {
						return "", errors.New("local index identity invalid")
					}
					for _, sql := range []string{"DROP INDEX CONCURRENTLY IF EXISTS idx_location_name_lower_lookup", "CREATE INDEX CONCURRENTLY idx_location_name_lower_lookup ON location_name (lower(name)) INCLUDE (location_id)"} {
						if _, err := conn.Exec(ctx, sql); err != nil {
							return "", errors.New("local concurrent index migration rejected")
						}
					}
				}
			}
		}
		autocommit := false
		for _, sql := range revision.Statements {
			switch sql {
			case "COMMIT;":
				if err := commit(); err != nil {
					return "", fmt.Errorf("local migration %s commit rejected", revision.ID)
				}
				autocommit = true
				continue
			case "BEGIN;":
				autocommit = false
				continue
			}
			// The original emitter supplies complete statements, including DO
			// bodies. Preserve its boundaries without splitting SQL on semicolons.
			if revision.ID == "0001" {
				sql = strings.Replace(sql, "CREATE TABLE alembic_version (", "CREATE TABLE IF NOT EXISTS alembic_version (", 1)
			}
			if autocommit {
				if _, err := conn.Exec(ctx, sql); err != nil {
					return "", fmt.Errorf("local migration %s autocommit SQL rejected", revision.ID)
				}
			} else {
				if tx == nil {
					tx, err = conn.Begin(ctx)
					if err != nil {
						return "", errors.New("local schema transaction rejected")
					}
				}
				if _, err := tx.Exec(ctx, sql); err != nil {
					return "", fmt.Errorf("local migration %s SQL rejected", revision.ID)
				}
			}
		}
		if autocommit {
			return "", errors.New("migration emitter left an open autocommit scope")
		}
	}
	if err := commit(); err != nil {
		return "", errors.New("local schema final commit rejected")
	}
	return manifest.Head, nil
}

func runLocalMigrate() error {
	dsn := os.Getenv("LOCAL_DATABASE_URL")
	if dsn == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	role := os.Getenv("CRAWLER_DB_ROLE")
	if role == "" {
		role = "deploy-migrate"
	}
	config, err := providerCutoverPoolConfig(dsn, role)
	if err != nil {
		return errors.New("local migration connection invalid")
	}
	delete(config.ConnConfig.RuntimeParams, "statement_timeout")
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	conn, err := pgx.ConnectConfig(ctx, config.ConnConfig)
	if err != nil {
		return errors.New("local migration database unavailable")
	}
	defer conn.Close(context.Background())
	head, err := migrateLocal(ctx, conn)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]string{"schema_revision": head})
}
