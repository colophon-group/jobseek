package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Exact retained query contracts: the cold wrapper owns quiescence and the
// administrative pool owns the original bounded principal/startup settings.
//
//go:embed b0-cutover-plan/boards.sql
var b0PlanBoardsSQL string

//go:embed b0-cutover-plan/activation-postings.sql
var b0PlanActivationPostingsSQL string

//go:embed b0-cutover-plan/rollback-postings.sql
var b0PlanRollbackPostingsSQL string

//go:embed b0-cutover-plan/fence-ids.sql
var b0PlanFenceIDsSQL string

type b0PlanBoard struct {
	ID, Slug, URL string
	Enabled       *bool
	Status        string
	Metadata      json.RawMessage
	CrawlerType   string
	Browser       *bool
	Interval      *int64
}

func b0PlanReadBoards(ctx context.Context, pool *pgxpool.Pool, slugs []string) ([]b0PlanBoard, error) {
	if pool == nil || len(slugs) == 0 || len(slugs) > 16 {
		return nil, errB0Cutover
	}
	rows, err := pool.Query(ctx, b0PlanBoardsSQL, slugs)
	if err != nil {
		return nil, errB0Cutover
	}
	values, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (b0PlanBoard, error) {
		var value b0PlanBoard
		err := row.Scan(&value.ID, &value.Slug, &value.URL, &value.Enabled, &value.Status, &value.Metadata, &value.CrawlerType, &value.Browser, &value.Interval)
		return value, err
	})
	if err != nil {
		return nil, errB0Cutover
	}
	return values, nil
}

type b0PlanPostgresPosting struct {
	ID, BoardID       string
	URL               *string
	Hash              *int64
	Active            *bool
	Next, LeasedUntil *time.Time
	LeaseActive       *bool
	Slug              string
	Enabled           *bool
	Status            string
	Browser           *bool
	Interval          *int64
}

func b0PlanReadPostings(ctx context.Context, pool *pgxpool.Pool, ids []string, rollback bool) ([]b0PlanPostgresPosting, error) {
	if pool == nil {
		return nil, errB0Cutover
	}
	if len(ids) == 0 {
		return []b0PlanPostgresPosting{}, nil
	}
	query := b0PlanActivationPostingsSQL
	if rollback {
		query = b0PlanRollbackPostingsSQL
	}
	rows, err := pool.Query(ctx, query, ids)
	if err != nil {
		return nil, errB0Cutover
	}
	values, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (b0PlanPostgresPosting, error) {
		var value b0PlanPostgresPosting
		fields := []any{&value.ID, &value.BoardID, &value.URL, &value.Hash, &value.Active, &value.Next, &value.LeasedUntil, &value.LeaseActive, &value.Slug}
		if rollback {
			fields = append(fields, &value.Enabled, &value.Status)
		}
		fields = append(fields, &value.Browser, &value.Interval)
		err := row.Scan(fields...)
		return value, err
	})
	if err != nil {
		return nil, errB0Cutover
	}
	return values, nil
}

func b0PlanReadFenceIDs(ctx context.Context, pool *pgxpool.Pool, identity b0CutoverIdentity) ([]string, error) {
	if pool == nil {
		return nil, errB0Cutover
	}
	rows, err := pool.Query(ctx, b0PlanFenceIDsSQL, identity.Shard, identity.Epoch)
	if err != nil {
		return nil, errB0Cutover
	}
	values, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return nil, errB0Cutover
	}
	return values, nil
}
