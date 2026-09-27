package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

var syncLocalCountsSQL = map[string]string{
	"location":   `SELECT unnest(location_ids)::text,COUNT(*) FROM job_posting WHERE is_active GROUP BY 1`,
	"occupation": `SELECT occupation_id::text,COUNT(*) FROM job_posting WHERE is_active AND occupation_id IS NOT NULL GROUP BY 1`,
	"seniority":  `SELECT seniority_id::text,COUNT(*) FROM job_posting WHERE is_active AND seniority_id IS NOT NULL GROUP BY 1`,
	"technology": `SELECT unnest(technology_ids)::text,COUNT(*) FROM job_posting WHERE is_active GROUP BY 1`,
}

func syncLocalCounts(ctx context.Context, pool *pgxpool.Pool, collection string) (map[string]int, error) {
	query, exists := syncLocalCountsSQL[collection]
	if !exists {
		return nil, errors.New("unsupported local count collection")
	}
	rows, err := pool.Query(ctx, query)
	if err != nil {
		return nil, errors.New("local taxonomy count query failed")
	}
	defer rows.Close()
	result := map[string]int{}
	for rows.Next() {
		var id *string
		var count int
		if err := rows.Scan(&id, &count); err != nil {
			return nil, errors.New("invalid local taxonomy count")
		}
		if id != nil {
			result[*id] = count
		}
	}
	if rows.Err() != nil {
		return nil, errors.New("local taxonomy count read failed")
	}
	return result, nil
}
func syncPostingCounts(ctx context.Context, pool *pgxpool.Pool, reader taxonomyReader, now time.Time) (map[string]map[string]int, map[string]int, error) {
	counts := map[string]map[string]int{}
	for _, spec := range []struct{ collection, field string }{{"location", "location_ids"}, {"occupation", "occupation_ids"}, {"seniority", "seniority_id"}, {"technology", "technology_ids"}} {
		values, err := fetchFacetCounts(ctx, reader.Client, reader.BaseURL, reader.Key, spec.field, postingBaseFilter)
		if err != nil {
			if ctx.Err() != nil {
				return nil, nil, ctx.Err()
			}
			slog.Warn("typesense.sync.facet_unavailable", "collection", spec.collection)
			values, err = syncLocalCounts(ctx, pool, spec.collection)
		}
		if err != nil {
			return nil, nil, err
		}
		counts[spec.collection] = values
	}
	company, err := fetchFacetCounts(ctx, reader.Client, reader.BaseURL, reader.Key, "company_id", postingBaseFilter)
	if err != nil {
		return nil, nil, errors.New("company active facet is unavailable")
	}
	counts["company"] = company
	year, err := fetchFacetCounts(ctx, reader.Client, reader.BaseURL, reader.Key, "company_id", fmt.Sprintf("%s && first_seen_at:>%d", postingFlowFilter, oneYearAgoEpoch(now)))
	if err != nil {
		return nil, nil, errors.New("company year facet is unavailable")
	}
	return counts, year, nil
}
func notifySyncTypeahead(ctx context.Context, client *http.Client, endpoint, token string) bool {
	if endpoint == "" || token == "" {
		slog.Info("invalidate.typeahead.skipped")
		return false
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, nil)
	if err != nil {
		slog.Warn("invalidate.typeahead.request_failed")
		return false
	}
	request.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(request)
	if err != nil {
		slog.Warn("invalidate.typeahead.request_failed")
		return false
	}
	defer response.Body.Close()
	if response.StatusCode >= 400 {
		slog.Warn("invalidate.typeahead.bad_status", "status", response.StatusCode)
		return false
	}
	slog.Info("invalidate.typeahead.done")
	return true
}
func publishSyncTaxonomies(ctx context.Context, publisher taxonomyPublisher, documents taxonomyDocuments) error {
	for _, collection := range []string{"location", "occupation", "seniority", "technology"} {
		if err := publisher.upsert(ctx, collection, documents[collection]); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			slog.Error("typesense.sync.taxonomy.failed", "collection", collection)
			continue
		}
		slog.Info("typesense.sync.taxonomy.synced", "collection", collection, "count", len(documents[collection]))
	}
	deleted, err := publisher.companies(ctx, documents["company"])
	if err != nil {
		return errors.New("Typesense company exact sync failed")
	}
	slog.Info("typesense.companies.synced", "expected_count", len(documents["company"]), "deleted_count", deleted)
	return nil
}
func runSyncTaxonomies() (runErr error) {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()
	defer func() {
		if ctx.Err() != nil {
			runErr = ctx.Err()
		}
	}()
	settings, err := loadExporterSettings()
	if err != nil {
		return err
	}
	config, err := pgxpool.ParseConfig(settings.DBURL)
	if err != nil {
		return errors.New("invalid taxonomy sync database configuration")
	}
	config.MaxConns = 1
	config.ConnConfig.ConnectTimeout = 15 * time.Second
	config.ConnConfig.RuntimeParams["application_name"] = "jobseek:crawler:deploy-sync|typesense-sync:local"
	config.ConnConfig.RuntimeParams["statement_timeout"] = "300000"
	config.ConnConfig.RuntimeParams["idle_in_transaction_session_timeout"] = "60000"
	pool, err := pgxpool.NewWithConfig(ctx, config)
	if err != nil {
		return errors.New("taxonomy sync database unavailable")
	}
	defer pool.Close()
	contract, err := loadTaxonomyContract()
	if err != nil {
		return err
	}
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return errors.New("taxonomy sync database unavailable")
	}
	documents, err := loadSyncTaxonomySnapshot(ctx, conn.Conn(), contract)
	conn.Release()
	if err != nil {
		return errors.New("taxonomy sync authority could not be read")
	}
	client := &http.Client{Timeout: 120 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	reader := taxonomyReader{Client: client, BaseURL: settings.TypesenseURL, Key: settings.OperationsKey}
	counts, year, err := syncPostingCounts(ctx, pool, reader, time.Now().UTC())
	if err != nil {
		return err
	}
	applySyncCounts(documents, counts, year)
	if err := publishSyncTaxonomies(ctx, newTaxonomyPublisher(reader), documents); err != nil {
		return err
	}
	if _, err := refreshCounts(ctx, false, pool, client, settings.TypesenseURL, settings.OperationsKey, time.Now().UTC()); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		slog.Error("typesense.sync.refresh_counts.failed")
	}
	notifySyncTypeahead(ctx, client, os.Getenv("WEB_INVALIDATE_URL"), os.Getenv("INTERNAL_REVALIDATE_TOKEN"))
	if ctx.Err() != nil {
		return ctx.Err()
	}
	slog.Info("typesense.sync.complete")
	return nil
}
