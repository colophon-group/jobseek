package main

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
)

// The selected fields mirror PostingSchema.select_typesense_changed_sql from
// the Python exporter. The company JOIN is part of the same statement
// snapshot, so a new company cannot produce an empty denormalized name.
const changedPostingsSQL = `SELECT to_jsonb(selected), selected.updated_at, selected.id::text
FROM (
    SELECT jp.id, jp.company_id, jp.source_url, jp.is_active, jp.titles,
           jp.locales, jp.location_ids, jp.location_types, jp.employment_type,
           jp.salary_min, jp.salary_max, jp.salary_currency, jp.salary_period,
           jp.salary_eur, jp.experience_min, jp.experience_max,
           jp.occupation_id, jp.seniority_id, jp.technology_ids,
           jp.description_r2_hash, jp.first_seen_at, jp.last_seen_at,
           jp.updated_at, c.name AS company_name, c.slug AS company_slug,
           c.icon AS company_icon
    FROM job_posting AS jp
    JOIN company AS c ON c.id = jp.company_id
    WHERE (jp.updated_at, jp.id) > ($1::timestamptz, $2::uuid)
      AND jp.updated_at < $4::timestamptz
    ORDER BY jp.updated_at, jp.id
    LIMIT $3
) AS selected`

type postingQuerier interface {
	Query(context.Context, string, ...any) (pgx.Rows, error)
}

// fetchPostings is read-only. Its caller may advance the durable cursor only
// after every projected document receives a Typesense acknowledgement.
func fetchPostings(ctx context.Context, db postingQuerier, after cursor, cutoff time.Time, limit int) ([]Row, cursor, error) {
	if limit < 1 || limit > 2000 {
		return nil, after, fmt.Errorf("posting batch limit must be 1..2000")
	}
	if !after.UpdatedAt.Before(cutoff) {
		return []Row{}, after, nil
	}
	rows, err := db.Query(ctx, changedPostingsSQL, after.UpdatedAt, after.ID, limit, cutoff)
	if err != nil {
		return nil, after, err
	}
	defer rows.Close()
	result := make([]Row, 0, limit)
	last := after
	for rows.Next() {
		var raw []byte
		var updatedAt time.Time
		var id string
		if err := rows.Scan(&raw, &updatedAt, &id); err != nil {
			return nil, after, err
		}
		var posting Row
		if err := json.Unmarshal(raw, &posting); err != nil {
			return nil, after, fmt.Errorf("decode posting row: %w", err)
		}
		if posting.ID != id {
			return nil, after, fmt.Errorf("posting row UUID differs from cursor UUID")
		}
		next := cursor{UpdatedAt: updatedAt, ID: id}
		if !last.before(next) || !updatedAt.Before(cutoff) {
			return nil, after, fmt.Errorf("posting batch violates CDC keyset order or cutoff")
		}
		result = append(result, posting)
		last = next
	}
	if err := rows.Err(); err != nil {
		return nil, after, err
	}
	return result, last, nil
}
