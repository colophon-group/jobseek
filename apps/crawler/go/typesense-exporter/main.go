package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"hash"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Projection consumes offline JSON; --check-maps and --shadow-batch read
// production inputs without writing a cursor or index. --run requires an
// explicit enable flag and exclusive ownership recorded in PostgreSQL.
func main() {
	if len(os.Args) == 2 && os.Args[1] == "--owner" {
		if err := printOwner(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 4 && os.Args[1] == "--transfer-owner" {
		if err := transferOwner(os.Args[2], os.Args[3]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "--healthcheck" {
		if err := healthcheck(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "--run" {
		if err := runExporter(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
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
	if len(os.Args) == 2 && os.Args[1] == "--project-batch" {
		if err := projectBatch(); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "usage: typesense-exporter [--run|--owner|--healthcheck|--check-maps|--shadow-batch|--project-batch|--transfer-owner python go|--transfer-owner go python]")
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

func projectBatch() error {
	var input struct {
		Rows []Row `json:"rows"`
		Maps Maps  `json:"maps"`
	}
	decoder := json.NewDecoder(os.Stdin)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		return fmt.Errorf("decode projection batch: %w", err)
	}
	if len(input.Rows) == 0 || len(input.Rows) > 2000 {
		return fmt.Errorf("projection batch must have 1..2000 rows")
	}
	docs := make([]map[string]any, 0, len(input.Rows))
	for _, row := range input.Rows {
		doc, err := project(row, input.Maps)
		if err != nil {
			return err
		}
		docs = append(docs, doc)
	}
	return json.NewEncoder(os.Stdout).Encode(docs)
}

func healthcheck() error {
	port := 9093
	if raw := os.Getenv("METRICS_PORT"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 65535 {
			return fmt.Errorf("METRICS_PORT must be valid")
		}
		port = parsed
	}
	client := &http.Client{Timeout: 3 * time.Second}
	response, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/health", port))
	if err != nil {
		return fmt.Errorf("exporter health request: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("exporter health returned HTTP %d", response.StatusCode)
	}
	return nil
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
	includeDocs := os.Getenv("GO_TYPESENSE_SHADOW_DOCS") == "1"
	var projected []map[string]any
	if includeDocs {
		projected = make([]map[string]any, 0, len(rows))
	}
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
		if includeDocs {
			projected = append(projected, doc)
		}
	}
	result := map[string]any{
		"rows": len(rows), "document_sha256": fmt.Sprintf("%x", digest.Sum(nil)),
		"cutoff": cutoff.UTC().Format(time.RFC3339Nano),
	}
	if includeDocs {
		result["documents"] = projected
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
