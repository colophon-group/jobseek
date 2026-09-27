package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"sort"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

type renameNameMaps map[string]map[int]*string

var renameNameQueries = map[string]string{
	"occupation": `SELECT o.id,n.name FROM occupation o JOIN occupation_name n ON n.occupation_id=o.id WHERE n.is_display AND n.locale='en'`,
	"seniority":  `SELECT s.id,n.name FROM seniority s JOIN seniority_name n ON n.seniority_id=s.id WHERE n.is_display AND n.locale='en'`,
	"technology": `SELECT id,name FROM technology`,
}
var renamePostingQueries = map[string]string{
	"occupation": `SELECT id::text,NULL::int[] FROM job_posting WHERE occupation_id=$1 AND ($2::uuid IS NULL OR id>$2::uuid) ORDER BY id LIMIT 1000`,
	"seniority":  `SELECT id::text,NULL::int[] FROM job_posting WHERE seniority_id=$1 AND ($2::uuid IS NULL OR id>$2::uuid) ORDER BY id LIMIT 1000`,
	"technology": `SELECT id::text,array_remove(technology_ids,NULL) FROM job_posting WHERE technology_ids @> ARRAY[$1::int] AND ($2::uuid IS NULL OR id>$2::uuid) ORDER BY id LIMIT 1000`,
}

func loadRenameNames(ctx context.Context, db taxonomyQuerier) (renameNameMaps, error) {
	names := renameNameMaps{}
	for _, kind := range []string{"occupation", "seniority", "technology"} {
		rows, err := db.Query(ctx, renameNameQueries[kind])
		if err != nil {
			return nil, errors.New("taxonomy rename names unavailable")
		}
		names[kind] = map[int]*string{}
		for rows.Next() {
			var id int
			var name *string
			if err := rows.Scan(&id, &name); err != nil {
				rows.Close()
				return nil, errors.New("invalid taxonomy rename names")
			}
			if old, exists := names[kind][id]; exists && !sameRenameName(old, name) {
				rows.Close()
				return nil, errors.New("conflicting taxonomy rename names")
			}
			names[kind][id] = name
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return nil, errors.New("taxonomy rename names read failed")
		}
	}
	return names, nil
}
func sameRenameName(left, right *string) bool {
	if left == nil || right == nil {
		return left == right
	}
	return *left == *right
}
func changedRenameIDs(before, after map[int]*string) []int {
	ids := []int{}
	for id, name := range after {
		old := before[id]
		if old != nil && *old != "" && !sameRenameName(old, name) {
			ids = append(ids, id)
		}
	}
	sort.Ints(ids)
	return ids
}
func decodeRenameInput(input io.Reader) (renameNameMaps, error) {
	const limit = 8 << 20
	data, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil || len(data) > limit || !utf8.Valid(data) {
		return nil, errors.New("invalid taxonomy rename input")
	}
	var payload struct {
		Before renameNameMaps `json:"before"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&payload); err != nil || payload.Before == nil {
		return nil, errors.New("invalid taxonomy rename input")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return nil, errors.New("trailing taxonomy rename input")
	}
	for kind := range payload.Before {
		if _, exists := renameNameQueries[kind]; !exists {
			return nil, errors.New("unsupported taxonomy rename kind")
		}
	}
	return payload.Before, nil
}
func snapshotRenameNames() error {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return errors.New("taxonomy snapshot connection unavailable")
	}
	defer conn.Release()
	if _, err := conn.Exec(ctx, "SET application_name='jobseek:crawler:deploy-sync|taxonomy-snapshot:local'"); err != nil {
		return errors.New("taxonomy snapshot session unavailable")
	}
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return errors.New("taxonomy snapshot unavailable")
	}
	defer tx.Rollback(context.Background())
	names, err := loadRenameNames(ctx, tx)
	if err != nil {
		return err
	}
	if err := tx.Commit(ctx); err != nil {
		return errors.New("taxonomy snapshot failed")
	}
	return json.NewEncoder(os.Stdout).Encode(names)
}

type renamePosting struct {
	ID            string
	TechnologyIDs []int
}

func renameDocuments(kind string, name *string, postings []renamePosting, technologyNames map[int]*string) []map[string]any {
	docs := []map[string]any{}
	for _, posting := range postings {
		doc := map[string]any{"id": posting.ID}
		if kind == "technology" {
			names := []string{}
			for _, id := range posting.TechnologyIDs {
				if value := technologyNames[id]; value != nil && *value != "" {
					names = append(names, *value)
				}
			}
			if len(names) == 0 {
				continue
			}
			doc["technology_names"] = names
		} else {
			if name == nil {
				doc[kind+"_name"] = nil
			} else {
				doc[kind+"_name"] = *name
			}
		}
		docs = append(docs, doc)
	}
	return docs
}
func applyRenameUpdates(ctx context.Context, conn *pgx.Conn, reader taxonomyReader, before renameNameMaps) error {
	if before == nil {
		return nil
	}
	return withCursorFence(ctx, conn, func() error {
		after, err := loadRenameNames(ctx, conn)
		if err != nil {
			return err
		}
		for _, kind := range []string{"occupation", "seniority", "technology"} {
			ids := changedRenameIDs(before[kind], after[kind])
			for _, id := range ids {
				var afterID *string
				updated, rejected := 0, 0
				for {
					rows, err := conn.Query(ctx, renamePostingQueries[kind], id, afterID)
					if err != nil {
						return errors.New("taxonomy rename posting query failed")
					}
					postings := []renamePosting{}
					for rows.Next() {
						var posting renamePosting
						if err := rows.Scan(&posting.ID, &posting.TechnologyIDs); err != nil {
							rows.Close()
							return errors.New("invalid taxonomy rename posting")
						}
						postings = append(postings, posting)
					}
					err = rows.Err()
					rows.Close()
					if err != nil {
						return errors.New("taxonomy rename posting read failed")
					}
					if len(postings) == 0 {
						break
					}
					docs := renameDocuments(kind, after[kind][id], postings, after["technology"])
					if len(docs) > 0 {
						failures, err := importCollectionDocs(ctx, reader.Client, reader.BaseURL, reader.Key, "job_posting", "update", docs)
						if err != nil {
							return errors.New("taxonomy rename acknowledgement unavailable")
						}
						// Retain existing best-effort posting rename semantics (new, not-yet-
						// indexed postings may return 404). Full CDC/reconciliation owns repair.
						updated += len(docs) - len(failures)
						rejected += len(failures)
					}
					next := postings[len(postings)-1].ID
					if afterID != nil && next <= *afterID {
						return errors.New("taxonomy rename keyset did not advance")
					}
					afterID = &next
				}
				slog.Info("typesense.rename.complete", "kind", kind, "updated", updated, "rejected", rejected)
			}
		}
		return nil
	})
}
