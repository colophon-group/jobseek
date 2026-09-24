package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"hash"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Both commands are dark and read-only. Projection consumes offline JSON;
// --check-maps loads production taxonomy inputs but writes no cursor or index.
func main() {
	if len(os.Args) == 2 && os.Args[1] == "--check-maps" {
		if err := checkMaps(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "--shadow-batch" {
		if err := shadowBatch(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "usage: typesense-exporter [--check-maps|--shadow-batch]")
		os.Exit(2)
	}
	var input struct {
		Row  Row  `json:"row"`
		Maps Maps `json:"maps"`
	}
	decoder := json.NewDecoder(os.Stdin)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	doc, err := project(input.Row, input.Maps)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if err := json.NewEncoder(os.Stdout).Encode(doc); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func checkMaps() error {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	maps, err := loadMaps(ctx, pool)
	if err != nil {
		return fmt.Errorf("load taxonomy maps: %w", err)
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]int{
		"locations": len(maps.LocationNames), "location_ancestors": len(maps.LocationAncestors),
		"occupations": len(maps.OccupationNames), "occupation_ancestors": len(maps.OccupationAncestors),
		"seniorities": len(maps.SeniorityNames), "technologies": len(maps.TechnologyNames),
	})
}

func openPool(ctx context.Context) (*pgxpool.Pool, error) {
	connectionString := os.Getenv("LOCAL_DATABASE_URL")
	if connectionString == "" {
		return nil, fmt.Errorf("LOCAL_DATABASE_URL is required")
	}
	config, err := pgxpool.ParseConfig(connectionString)
	if err != nil {
		return nil, fmt.Errorf("invalid local database configuration")
	}
	config.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return nil, fmt.Errorf("could not open local database pool")
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("local database is unavailable")
	}
	return pool, nil
}

func shadowBatch() error {
	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	maps, err := loadMaps(ctx, pool)
	if err != nil {
		return fmt.Errorf("load taxonomy maps: %w", err)
	}
	var cutoff time.Time
	if err := pool.QueryRow(ctx, "SELECT clock_timestamp()").Scan(&cutoff); err != nil {
		return fmt.Errorf("capture read-only shadow cutoff: %w", err)
	}
	start := cursor{UpdatedAt: cutoff.Add(-6 * time.Hour), ID: zeroUUID}
	rows, _, err := fetchPostings(ctx, pool, start, cutoff, 200)
	if err != nil {
		return fmt.Errorf("read shadow postings: %w", err)
	}
	var digest hash.Hash = sha256.New()
	for _, row := range rows {
		doc, err := project(row, maps)
		if err != nil {
			return fmt.Errorf("project shadow posting: %w", err)
		}
		encoded, err := json.Marshal(doc)
		if err != nil {
			return fmt.Errorf("encode shadow document: %w", err)
		}
		digest.Write(encoded)
		digest.Write([]byte{'\n'})
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"rows": len(rows), "document_sha256": fmt.Sprintf("%x", digest.Sum(nil)),
		"cutoff": cutoff.UTC().Format(time.RFC3339Nano),
	})
}
