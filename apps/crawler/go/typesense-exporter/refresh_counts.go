package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"sort"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

const countRefreshTargetsSQL = `
SELECT 'location' AS collection, l.id::text AS document_id, l.id::text AS facet_value
FROM location l
UNION ALL
SELECT DISTINCT
       'occupation' AS collection,
       o.id::text || '-' || n.locale AS document_id,
       o.id::text AS facet_value
FROM occupation o
JOIN occupation_name n ON n.occupation_id = o.id
WHERE n.is_display AND n.locale <> '*'
UNION ALL
SELECT DISTINCT
       'seniority' AS collection,
       s.id::text || '-' || n.locale AS document_id,
       s.id::text AS facet_value
FROM seniority s
JOIN seniority_name n ON n.seniority_id = s.id
WHERE n.is_display AND n.locale <> '*'
UNION ALL
SELECT 'technology' AS collection, t.id::text AS document_id, t.id::text AS facet_value
FROM technology t
UNION ALL
SELECT 'company' AS collection, c.id::text AS document_id, c.id::text AS facet_value
FROM company c
`

const (
	postingBaseFilter = "is_active:true && has_content:!=false"
	postingFlowFilter = "has_content:!=false"
	maxFacetValues    = 100000
	refreshBatchSize  = 1000
	maxFacetResponse  = 64 << 20
	refreshTimeBudget = 30 * time.Minute
	refreshAttempts   = 4 // Initial attempt plus three retries, matching the Python maintenance client.
)

var refreshCollections = []string{"location", "occupation", "seniority", "technology", "company"}

func retryRefreshRequest[T any](ctx context.Context, operation string, call func() (T, error)) (T, error) {
	var zero T
	for attempt := 1; attempt <= refreshAttempts; attempt++ {
		value, err := call()
		if err == nil {
			return value, nil
		}
		if attempt == refreshAttempts || ctx.Err() != nil {
			return zero, err
		}
		slog.Warn("typesense.refresh_retry", "operation", operation, "attempt", attempt, "error", err)
		wait := time.Duration(1<<(attempt-1)) * 500 * time.Millisecond
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return zero, ctx.Err()
		case <-timer.C:
		}
	}
	return zero, errors.New("unreachable Typesense refresh retry state")
}

func loadCountTargets(ctx context.Context, pool *pgxpool.Pool) (map[string]map[string]string, error) {
	targets := make(map[string]map[string]string, len(refreshCollections))
	for _, collection := range refreshCollections {
		targets[collection] = make(map[string]string)
	}
	rows, err := pool.Query(ctx, countRefreshTargetsSQL)
	if err != nil {
		return nil, fmt.Errorf("read count-refresh authority: %w", err)
	}
	defer rows.Close()
	for rows.Next() {
		var collection, documentID, facetValue string
		if err := rows.Scan(&collection, &documentID, &facetValue); err != nil {
			return nil, err
		}
		entries, ok := targets[collection]
		if !ok || documentID == "" || facetValue == "" {
			return nil, fmt.Errorf("invalid count-refresh target for %q", collection)
		}
		if previous, exists := entries[documentID]; exists && previous != facetValue {
			return nil, fmt.Errorf("conflicting %s count-refresh target %q", collection, documentID)
		}
		entries[documentID] = facetValue
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for _, collection := range refreshCollections {
		if len(targets[collection]) == 0 {
			return nil, fmt.Errorf("count-refresh authority is empty for %s", collection)
		}
	}
	return targets, nil
}

func fetchFacetCounts(ctx context.Context, client *http.Client, baseURL, apiKey, field, filter string) (map[string]int, error) {
	if client == nil || apiKey == "" {
		return nil, errors.New("Typesense client and operations key are required")
	}
	u, err := url.Parse(baseURL)
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return nil, errors.New("Typesense base URL must be an HTTP(S) origin")
	}
	u.Path = "/collections/job_posting/documents/search"
	q := u.Query()
	q.Set("q", "*")
	q.Set("query_by", "title")
	q.Set("filter_by", filter)
	q.Set("facet_by", field)
	q.Set("max_facet_values", fmt.Sprint(maxFacetValues))
	q.Set("facet_strategy", "exhaustive")
	q.Set("per_page", "0")
	u.RawQuery = q.Encode()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-TYPESENSE-API-KEY", apiKey)
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxFacetResponse+1))
	if err != nil {
		return nil, err
	}
	if len(body) > maxFacetResponse || resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("Typesense %s facet response exceeded limit or returned HTTP %d", field, resp.StatusCode)
	}
	return parseFacetCounts(body, field)
}

func parseFacetCounts(body []byte, field string) (map[string]int, error) {
	var envelope map[string]json.RawMessage
	if err := json.Unmarshal(body, &envelope); err != nil {
		return nil, err
	}
	raw, ok := envelope["facet_counts"]
	if !ok || string(raw) == "null" {
		return nil, fmt.Errorf("Typesense facet response is missing facet_counts for %s", field)
	}
	var facets []map[string]json.RawMessage
	if err := json.Unmarshal(raw, &facets); err != nil || facets == nil {
		return nil, fmt.Errorf("Typesense facet response has invalid facet_counts for %s", field)
	}
	result := make(map[string]int)
	if len(facets) == 0 {
		return result, nil
	}
	matching := 0
	for _, facet := range facets {
		var name string
		if err := json.Unmarshal(facet["field_name"], &name); err != nil || name != field {
			continue
		}
		matching++
		var counts []map[string]json.RawMessage
		if err := json.Unmarshal(facet["counts"], &counts); err != nil || counts == nil {
			return nil, fmt.Errorf("Typesense facet response has invalid counts for %s", field)
		}
		for _, entry := range counts {
			var value string
			var count int
			rawValue, valuePresent := entry["value"]
			if !valuePresent || string(rawValue) == "null" {
				return nil, fmt.Errorf("Typesense facet response has invalid value for %s", field)
			}
			if err := json.Unmarshal(rawValue, &value); err != nil || value == "" {
				return nil, fmt.Errorf("Typesense facet response has invalid value for %s", field)
			}
			rawCount, countPresent := entry["count"]
			if !countPresent || string(rawCount) == "null" {
				return nil, fmt.Errorf("Typesense facet response has invalid count for %s", field)
			}
			if err := json.Unmarshal(rawCount, &count); err != nil || count < 0 {
				return nil, fmt.Errorf("Typesense facet response has invalid count for %s", field)
			}
			if _, exists := result[value]; exists {
				return nil, fmt.Errorf("Typesense facet response has duplicate value for %s", field)
			}
			result[value] = count
		}
	}
	if matching != 1 {
		return nil, fmt.Errorf("Typesense facet response did not contain exactly one %s facet", field)
	}
	return result, nil
}

func oneYearAgoEpoch(now time.Time) int64 {
	now = now.UTC()
	day := now.Day()
	if now.Month() == time.February && day == 29 {
		day = 28
	}
	return time.Date(now.Year()-1, now.Month(), day, now.Hour(), now.Minute(), now.Second(), now.Nanosecond(), time.UTC).Unix()
}

func countDocs(targets map[string]string, active, year map[string]int, company bool) []map[string]any {
	ids := make([]string, 0, len(targets))
	for id := range targets {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	docs := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		value := targets[id]
		count := active[value]
		doc := map[string]any{"id": id, "active_posting_count": count}
		if company {
			doc["year_posting_count"] = year[value]
		} else {
			doc["has_active_postings"] = count > 0
		}
		docs = append(docs, doc)
	}
	return docs
}

type refreshSummary struct {
	Counts      map[string]int    `json:"counts"`
	Digests     map[string]string `json:"digests"`
	ActiveTotal map[string]int64  `json:"active_total"`
	YearTotal   int64             `json:"year_total"`
}

type refreshSnapshot struct {
	Targets map[string]map[string]string `json:"targets"`
	Facets  map[string]map[string]int    `json:"facets"`
	Year    map[string]int               `json:"year"`
}

func projectRefreshSnapshot(snapshot refreshSnapshot) (map[string][]map[string]any, refreshSummary, error) {
	if snapshot.Targets == nil || snapshot.Facets == nil || snapshot.Year == nil {
		return nil, refreshSummary{}, errors.New("count-refresh snapshot is incomplete")
	}
	summary := refreshSummary{Counts: make(map[string]int), Digests: make(map[string]string), ActiveTotal: make(map[string]int64)}
	docsByCollection := make(map[string][]map[string]any, len(refreshCollections))
	for _, collection := range refreshCollections {
		targets := snapshot.Targets[collection]
		active, facetPresent := snapshot.Facets[collection]
		if len(targets) == 0 || !facetPresent || active == nil {
			return nil, refreshSummary{}, fmt.Errorf("count-refresh snapshot is missing %s authority or facet", collection)
		}
		docs := countDocs(targets, active, snapshot.Year, collection == "company")
		for _, doc := range docs {
			summary.ActiveTotal[collection] += int64(doc["active_posting_count"].(int))
			if collection == "company" {
				summary.YearTotal += int64(doc["year_posting_count"].(int))
			}
		}
		encoded, err := json.Marshal(docs)
		if err != nil {
			return nil, refreshSummary{}, err
		}
		digest := sha256.Sum256(encoded)
		summary.Counts[collection] = len(docs)
		summary.Digests[collection] = hex.EncodeToString(digest[:])
		docsByCollection[collection] = docs
	}
	return docsByCollection, summary, nil
}

func projectRefreshSnapshotCLI() error {
	decoder := json.NewDecoder(io.LimitReader(os.Stdin, 64<<20))
	decoder.DisallowUnknownFields()
	var snapshot refreshSnapshot
	if err := decoder.Decode(&snapshot); err != nil {
		return fmt.Errorf("decode count-refresh snapshot: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); err != io.EOF {
		return errors.New("count-refresh snapshot has trailing data")
	}
	_, summary, err := projectRefreshSnapshot(snapshot)
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(summary)
}

func refreshCounts(ctx context.Context, dryRun bool, pool *pgxpool.Pool, client *http.Client, baseURL, apiKey string, now time.Time) (refreshSummary, error) {
	targets, err := loadCountTargets(ctx, pool)
	if err != nil {
		return refreshSummary{}, err
	}
	fields := []struct{ collection, field, filter string }{
		{"location", "location_ids", postingBaseFilter},
		{"occupation", "occupation_ids", postingBaseFilter},
		{"seniority", "seniority_id", postingBaseFilter},
		{"technology", "technology_ids", postingBaseFilter},
		{"company", "company_id", postingBaseFilter},
	}
	facets := make(map[string]map[string]int, len(fields))
	for _, spec := range fields {
		facets[spec.collection], err = retryRefreshRequest(ctx, "facet:"+spec.field, func() (map[string]int, error) {
			return fetchFacetCounts(ctx, client, baseURL, apiKey, spec.field, spec.filter)
		})
		if err != nil {
			return refreshSummary{}, fmt.Errorf("read %s facet: %w", spec.field, err)
		}
	}
	year, err := retryRefreshRequest(ctx, "facet:company_id:year", func() (map[string]int, error) {
		return fetchFacetCounts(ctx, client, baseURL, apiKey, "company_id", fmt.Sprintf("%s && first_seen_at:>%d", postingFlowFilter, oneYearAgoEpoch(now)))
	})
	if err != nil {
		return refreshSummary{}, fmt.Errorf("read company year facet: %w", err)
	}
	docsByCollection, summary, err := projectRefreshSnapshot(refreshSnapshot{Targets: targets, Facets: facets, Year: year})
	if err != nil {
		return refreshSummary{}, err
	}
	for _, collection := range refreshCollections {
		docs := docsByCollection[collection]
		if !dryRun {
			for start := 0; start < len(docs); start += refreshBatchSize {
				end := min(start+refreshBatchSize, len(docs))
				failures, err := retryRefreshRequest(ctx, "update:"+collection, func() (map[string]importFailure, error) {
					return importCollectionDocs(ctx, client, baseURL, apiKey, collection, "update", docs[start:end])
				})
				if err != nil {
					return refreshSummary{}, fmt.Errorf("update %s counts: %w", collection, err)
				}
				if len(failures) != 0 {
					return refreshSummary{}, fmt.Errorf("update %s counts: %d rejected documents", collection, len(failures))
				}
			}
		}
	}
	return summary, nil
}

func runRefreshCounts(dryRun bool) (runErr error) {
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))
	started := time.Now()
	mode := "refresh-typesense"
	if dryRun {
		mode = "shadow-refresh-typesense"
	}
	slog.Info("cron.start", "job", mode)
	defer func() {
		if runErr != nil {
			slog.Error("cron.complete", "job", mode, "status", "failure", "duration_s", time.Since(started).Seconds(), "error", runErr)
			return
		}
		slog.Info("cron.complete", "job", mode, "status", "success", "duration_s", time.Since(started).Seconds())
	}()
	settings, err := loadExporterSettings()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), refreshTimeBudget)
	defer cancel()
	pool, err := openPool(ctx)
	if err != nil {
		return err
	}
	defer pool.Close()
	client := &http.Client{Timeout: 30 * time.Second}
	summary, err := refreshCounts(ctx, dryRun, pool, client, settings.TypesenseURL, settings.OperationsKey, time.Now().UTC())
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(summary)
}
