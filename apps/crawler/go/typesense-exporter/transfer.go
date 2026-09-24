package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
)

// transferOwner is an operator-only compare-and-swap. Both exporters check
// the owner while holding this same fence before every import and cursor save.
// A transfer waits for an in-flight tick to finish; the old runtime then
// observes its lost ownership before it can start another one.
func transferOwner(expected, next string) error {
	if os.Getenv("GO_TYPESENSE_EXPORTER_TRANSFER") != "1" {
		return errors.New("GO_TYPESENSE_EXPORTER_TRANSFER=1 is required")
	}
	if !((expected == "python" && next == "go") || (expected == "go" && next == "python")) {
		return errors.New("transfer must be python go or go python")
	}
	dbURL := os.Getenv("LOCAL_DATABASE_URL")
	if dbURL == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dbURL)
	if err != nil {
		return errors.New("could not connect to local PostgreSQL")
	}
	defer conn.Close(context.Background())
	err = withCursorFence(ctx, conn, func() error {
		current, err := loadOwner(ctx, conn)
		if err != nil {
			return err
		}
		if current != expected {
			return fmt.Errorf("Typesense exporter owner is %q, expected %q", current, expected)
		}
		var savedCursor string
		if err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key = $1", typesenseCursorKey).Scan(&savedCursor); err != nil {
			return fmt.Errorf("existing Typesense cursor is required for transfer: %w", err)
		}
		if savedCursor == "" {
			return errors.New("existing Typesense cursor is empty")
		}
		if _, err := parseCursor(savedCursor); err != nil {
			return fmt.Errorf("Typesense cursor is not compatible with Go: %w", err)
		}
		_, err = conn.Exec(ctx,
			"INSERT INTO exporter_state (key, value, updated_at) VALUES ($1, $2, now()) "+
				"ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
			typesenseOwnerKey, next)
		if err != nil {
			return fmt.Errorf("transfer Typesense exporter ownership: %w", err)
		}
		return nil
	})
	if err != nil {
		return err
	}
	fmt.Printf("Typesense posting exporter owner: %s -> %s\n", expected, next)
	return nil
}

func loadOwner(ctx context.Context, conn *pgx.Conn) (string, error) {
	var owner string
	err := conn.QueryRow(ctx, "SELECT value FROM exporter_state WHERE key = $1", typesenseOwnerKey).Scan(&owner)
	if errors.Is(err, pgx.ErrNoRows) {
		return "python", nil
	}
	if err != nil {
		return "", fmt.Errorf("read Typesense exporter owner: %w", err)
	}
	if owner != "python" && owner != "go" {
		return "", fmt.Errorf("unknown Typesense exporter owner %q", owner)
	}
	return owner, nil
}

// printOwner selects the process at container start. The selected process
// separately checks ownership inside the fence before any write, so a
// concurrent handoff cannot make this unlocked startup read unsafe.
func printOwner() error {
	dbURL := os.Getenv("LOCAL_DATABASE_URL")
	if dbURL == "" {
		return errors.New("LOCAL_DATABASE_URL is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := pgx.Connect(ctx, dbURL)
	if err != nil {
		return errors.New("could not connect to local PostgreSQL")
	}
	defer conn.Close(context.Background())
	owner, err := loadOwner(ctx, conn)
	if err != nil {
		return err
	}
	fmt.Println(owner)
	return nil
}
