package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"time"

	"github.com/jackc/pgx/v5"
)

type registryBoard struct {
	Company, URL, Monitor, Metadata, Domain string
	Slug                                    *string
	MonitorBrowser, ScraperBrowser          bool
}

func prepareRegistryBoards(table registryTable) ([]registryBoard, error) {
	routes, err := loadRegistryRoutes()
	if err != nil {
		return nil, err
	}
	boards := []registryBoard{}
	for i, row := range table.Rows {
		for _, key := range []string{"company_slug", "board_url", "monitor_type"} {
			if row[key] == nil {
				return nil, fmt.Errorf("board row %d has null or missing %s", i+1, key)
			}
		}
		metadata, err := registryBoardMetadata(row)
		if err != nil {
			// Match the existing invalid-row policy, without logging configuration
			// payloads. If every row is skipped, do not disable the live registry.
			slog.Error("sync.board.invalid_config", "row", i+1)
			continue
		}
		body, err := taxonomyCanonicalJSON(metadata, true)
		if err != nil {
			return nil, err
		}
		scrType, _ := metadata["scraper_type"].(string)
		scrConfig, _ := metadata["scraper_config"].(map[string]any)
		monitor, rawURL := registryText(row, "monitor_type"), registryText(row, "board_url")
		boards = append(boards, registryBoard{Company: registryText(row, "company_slug"), URL: rawURL, Monitor: monitor, Metadata: string(body), Domain: registryThrottleKey(monitor, rawURL, metadata, routes), Slug: registryOptional(row, "board_slug"), MonitorBrowser: registryMonitorBrowser(monitor, metadata), ScraperBrowser: registryScraperBrowser(scrType, scrConfig, routes)})
	}
	return boards, nil
}

func executeRegistryPlan(ctx context.Context, tx pgx.Tx, plan registryPlan) error {
	for _, statement := range plan {
		sql, err := registrySQL(statement.Key)
		if err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, sql, statement.Args...); err != nil {
			return fmt.Errorf("registry statement %s: %w", statement.Key, err)
		}
	}
	return nil
}

// Return only deferred effects. The caller must commit the encompassing local
// transaction before publishing them to Redis or Typesense.
func stageRegistryBoards(ctx context.Context, tx pgx.Tx, boards []registryBoard, clock func() time.Time) (boardSyncInput, error) {
	effects := boardSyncInput{Schedules: []boardSyncSchedule{}, Orphans: [][]string{}}
	if len(boards) == 0 {
		return effects, nil
	}
	companies, urls, monitors, metadata, domains := []string{}, []string{}, []string{}, []string{}, []string{}
	slugs := []*string{}
	checks, scrapes := []int32{}, []int32{}
	monBrowser, scrBrowser, enabled := []bool{}, []bool{}, []bool{}
	for _, b := range boards {
		companies = append(companies, b.Company)
		slugs = append(slugs, b.Slug)
		urls = append(urls, b.URL)
		monitors = append(monitors, b.Monitor)
		metadata = append(metadata, b.Metadata)
		domains = append(domains, b.Domain)
		checks = append(checks, 60)
		scrapes = append(scrapes, 24)
		monBrowser = append(monBrowser, b.MonitorBrowser)
		scrBrowser = append(scrBrowser, b.ScraperBrowser)
		enabled = append(enabled, true)
	}
	sql, err := registrySQL("_FETCH_BOARD_COMPANY_REHOMES_LOCAL")
	if err != nil {
		return effects, err
	}
	rows, err := tx.Query(ctx, sql, companies, slugs, urls)
	if err != nil {
		return effects, err
	}
	rehomes := map[string]string{}
	rehomeIDs, rehomeCompanies := []string{}, []string{}
	for rows.Next() {
		var id, company string
		if err = rows.Scan(&id, &company); err != nil {
			rows.Close()
			return effects, err
		}
		if prior, exists := rehomes[id]; exists {
			if prior != company {
				rows.Close()
				return effects, errors.New("board resolves to multiple companies")
			}
			continue
		}
		rehomes[id] = company
		rehomeIDs = append(rehomeIDs, id)
		rehomeCompanies = append(rehomeCompanies, company)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return effects, err
	}
	plan := registryPlan{}
	plan.add("_REALIGN_RENAMED_BOARD_URLS_LOCAL", companies, slugs, urls)
	if err = executeRegistryPlan(ctx, tx, plan); err != nil {
		return effects, err
	}
	sql, err = registrySQL("_UPSERT_BOARD_LOCAL")
	if err != nil {
		return effects, err
	}
	rows, err = tx.Query(ctx, sql, companies, slugs, urls, monitors, metadata, checks, scrapes, domains, monBrowser, scrBrowser, enabled)
	if err != nil {
		return effects, err
	}
	type authoritativeBoard struct {
		ID, Company, URL, Status string
		Metadata                 []byte
		Due                      *time.Time
	}
	byURL := map[string]authoritativeBoard{}
	for rows.Next() {
		var b authoritativeBoard
		if err = rows.Scan(&b.ID, &b.Company, &b.URL, &b.Metadata, &b.Due, &b.Status); err != nil {
			rows.Close()
			return effects, err
		}
		byURL[b.URL] = b
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return effects, err
	}
	now := clock()
	stamp := float64(now.Unix()) + float64(now.Nanosecond())/1e9
	for _, b := range boards {
		row, found := byURL[b.URL]
		if !found {
			return effects, errors.New("local board sync could not resolve every CSV company")
		}
		recovering := row.Status == "quarantined" || row.Status == "gone_pending" || row.Status == "gone"
		due := stamp
		if recovering && row.Due != nil && row.Due.After(now) {
			due = float64(row.Due.Unix()) + float64(row.Due.Nanosecond())/1e9
		}
		body, err := registryRedisMetadata(row.Metadata)
		if err != nil {
			return effects, err
		}
		slug := ""
		if b.Slug != nil {
			slug = *b.Slug
		}
		mon, scr := "0", "0"
		if b.MonitorBrowser {
			mon = "1"
		}
		if b.ScraperBrowser {
			scr = "1"
		}
		effects.Schedules = append(effects.Schedules, boardSyncSchedule{Domain: b.Domain, BoardID: row.ID, NextCheckAt: due, Browser: b.MonitorBrowser, FirstTime: !recovering, Config: map[string]string{"board_slug": slug, "board_url": b.URL, "crawler_type": b.Monitor, "company_id": row.Company, "metadata": body, "check_interval_minutes": "60", "scrape_interval_hours": "24", "throttle_key": b.Domain, "monitor_needs_browser": mon, "scraper_needs_browser": scr}})
	}
	plan = registryPlan{}
	if len(rehomeIDs) > 0 {
		plan.add("_REALIGN_BOARD_POSTING_COMPANIES_LOCAL", rehomeIDs, rehomeCompanies)
	}
	plan.add("_DISABLE_REMOVED_BOARDS_LOCAL", urls)
	if err = executeRegistryPlan(ctx, tx, plan); err != nil {
		return effects, err
	}
	sql, err = registrySQL("_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP")
	if err != nil {
		return effects, err
	}
	rows, err = tx.Query(ctx, sql)
	if err != nil {
		return effects, err
	}
	for rows.Next() {
		var id string
		var domain *string
		if err = rows.Scan(&id, &domain); err != nil {
			rows.Close()
			return effects, err
		}
		if domain != nil && *domain != "" {
			effects.Orphans = append(effects.Orphans, []string{*domain, id})
		}
	}
	err = rows.Err()
	rows.Close()
	return effects, err
}

// jsonb returns object keys in PostgreSQL order. Preserve that order while
// reproducing json.dumps' ASCII strings and comma/colon spacing for Redis.
func registryRedisMetadata(raw []byte) (string, error) {
	if len(raw) == 0 || bytes.Equal(bytes.TrimSpace(raw), []byte("null")) {
		return "{}", nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var output bytes.Buffer
	var write func() error
	write = func() error {
		token, err := decoder.Token()
		if err != nil {
			return err
		}
		if delimiter, ok := token.(json.Delim); ok {
			if delimiter != '{' && delimiter != '[' {
				return errors.New("unexpected metadata delimiter")
			}
			output.WriteRune(rune(delimiter))
			count := 0
			for decoder.More() {
				if count > 0 {
					output.WriteString(", ")
				}
				count++
				if delimiter == '{' {
					key, err := decoder.Token()
					if err != nil {
						return err
					}
					text, ok := key.(string)
					if !ok {
						return errors.New("invalid metadata key")
					}
					if err = taxonomyJSONString(&output, text, true); err != nil {
						return err
					}
					output.WriteString(": ")
				}
				if err = write(); err != nil {
					return err
				}
			}
			end, err := decoder.Token()
			if err != nil {
				return err
			}
			output.WriteRune(rune(end.(json.Delim)))
			return nil
		}
		return writeTaxonomyJSON(&output, token, true)
	}
	if err := write(); err != nil {
		return "", err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return "", errors.New("trailing metadata JSON")
	}
	return output.String(), nil
}
