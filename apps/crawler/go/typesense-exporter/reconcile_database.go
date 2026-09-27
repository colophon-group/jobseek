package main

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/jackc/pgx/v5"
)

type reconciliationDatabase struct {
	conn *pgx.Conn
	maps Maps
}

const reconciliationSelectSQL = `SELECT to_jsonb(selected) FROM (
    SELECT jp.id, jp.company_id, jp.source_url, jp.is_active, jp.titles,
           jp.locales, jp.location_ids, jp.location_types, jp.employment_type,
           jp.salary_min, jp.salary_max, jp.salary_currency, jp.salary_period,
           jp.salary_eur, jp.experience_min, jp.experience_max,
           jp.occupation_id, jp.seniority_id, jp.technology_ids,
           jp.tdm_reserved, jp.description_r2_hash, jp.first_seen_at, jp.last_seen_at,
           c.name AS company_name, c.slug AS company_slug, c.icon AS company_icon
    FROM job_posting jp JOIN company c ON c.id = jp.company_id WHERE `

func (d *reconciliationDatabase) fence(ctx context.Context, work func() error) error {
	return withCursorFence(ctx, d.conn, work)
}

func (d *reconciliationDatabase) read(ctx context.Context, query string, args ...any) ([]map[string]any, reconciliationSnapshot, error) {
	rows, err := d.conn.Query(ctx, query, args...)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	documents := make([]map[string]any, 0)
	snapshot := reconciliationSnapshot{}
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, nil, err
		}
		var row Row
		if err := json.Unmarshal(raw, &row); err != nil {
			return nil, nil, err
		}
		document, err := project(row, d.maps)
		if err != nil {
			return nil, nil, err
		}
		if err := addReconciliationDocument(snapshot, document); err != nil {
			return nil, nil, err
		}
		documents = append(documents, document)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, err
	}
	return documents, snapshot, nil
}

func (d *reconciliationDatabase) snapshot(ctx context.Context, partition int) (reconciliationSnapshot, error) {
	lower, upper, err := reconciliationBounds(partition)
	if err != nil {
		return nil, err
	}
	_, snapshot, err := d.read(ctx, reconciliationSelectSQL+`jp.id >= $1::uuid AND ($2::uuid IS NULL OR jp.id < $2::uuid) ORDER BY jp.id) selected`, lower, upper)
	return snapshot, err
}

func (d *reconciliationDatabase) documents(ctx context.Context, ids []string) ([]map[string]any, reconciliationSnapshot, error) {
	if len(ids) == 0 {
		return []map[string]any{}, reconciliationSnapshot{}, nil
	}
	return d.read(ctx, reconciliationSelectSQL+`jp.id = ANY($1::uuid[]) ORDER BY jp.id) selected`, ids)
}

func (d *reconciliationDatabase) existing(ctx context.Context, ids []string) (reconciliationIDs, error) {
	rows, err := d.conn.Query(ctx, `SELECT id::text FROM job_posting WHERE id = ANY($1::uuid[])`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := reconciliationIDs{}
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		if _, found := result[id]; found {
			return nil, errors.New("duplicate local reconciliation ID")
		}
		result[id] = struct{}{}
	}
	return result, rows.Err()
}
