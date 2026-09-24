package main

import (
	"context"
	"encoding/json"
	"fmt"
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
	if len(os.Args) != 1 {
		fmt.Fprintln(os.Stderr, "usage: typesense-exporter [--check-maps]")
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
	connectionString := os.Getenv("LOCAL_DATABASE_URL")
	if connectionString == "" {
		return fmt.Errorf("LOCAL_DATABASE_URL is required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	config, err := pgxpool.ParseConfig(connectionString)
	if err != nil {
		return fmt.Errorf("invalid local database configuration")
	}
	config.MaxConns = 1
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return fmt.Errorf("could not open local database pool")
	}
	defer pool.Close()
	if err := pool.Ping(ctx); err != nil {
		return fmt.Errorf("local database is unavailable")
	}
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
