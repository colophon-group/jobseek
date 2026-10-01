package queue

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"errors"
	"math/rand/v2"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Frozen URL-identity Python monitor statements. Greenhouse does not emit an
// explicit provider identity, so it uses this existing globally unique URL lane.
//
//go:embed rich_monitor_diff.sql
var richMonitorDiffSQL string

//go:embed rich_monitor_insert.sql
var richMonitorInsertSQL string

//go:embed rich_monitor_description.sql
var richMonitorDescriptionSQL string

// These detached nullable values form the queue writer's persistence boundary.
// The native processor remains outside this package so sharing queue/reaper
// authority does not link enrichment models into maintenance/export services.
type GreenhouseRichFields struct {
	EmploymentType               *string
	Titles, Locales              []string
	LocationIDs                  []int64
	LocationTypes                []string
	TechnologyIDs                []int64
	SalaryMin, SalaryMax         *int64
	SalaryCurrency, SalaryPeriod *string
	SalaryEUR                    *int64
	ExperienceMin, ExperienceMax *float64
	OccupationID, SeniorityID    *int64
}

type GreenhouseRichDescription struct {
	HTML, Locale string
	Hash         int64
}

type GreenhouseRichContent struct {
	Fields      GreenhouseRichFields
	Description *GreenhouseRichDescription
	Enrich      bool
}

// GreenhouseRichPosting contains a URL accepted by inventory normalization and
// native rich preparation performed before acquiring a database transaction.
// It is a posting batch, not proof that the complete inventory was observed.
type GreenhouseRichPosting struct {
	URL     string
	Content *GreenhouseRichContent
}

type GreenhouseRichBatchResult struct {
	Inserted, Touched, Relisted, Foreign, ForeignRelisted, Deduplicated int
}

// WriteGreenhouseRichBatch commits at most one Python-sized 500-posting chunk
// under the installed native ownership and exact Redis/PG claim fence. It never
// completes a board cycle, delists absent postings, advances board metadata or
// acknowledges a claim. Finalization must separately prove inventory completeness
// and commit the canonical monitor schedule before settlement.
func (a *Authority) WriteGreenhouseRichBatch(ctx context.Context, claim *Claim, batch []GreenhouseRichPosting) (*GreenhouseRichBatchResult, error) {
	if !a.valid(claim) || a.ownership == nil || claim.task.Kind != Monitor || len(batch) < 1 || len(batch) > 500 {
		return nil, ErrConfiguration
	}
	profile, err := InspectGreenhouseMonitor(claim.task.ID, claim.task.Config)
	if err != nil {
		return nil, err
	}
	urls := make([]string, 0, len(batch))
	byURL := make(map[string]*GreenhouseRichContent, len(batch))
	for _, posting := range batch {
		// Inventory filtering/normalization is the caller's earlier stage. No
		// ambiguous, absent or detail-enrichment records may reach this callback.
		if posting.URL == "" || strings.ContainsRune(posting.URL, 0) || posting.Content == nil || posting.Content.Enrich || posting.Content.Fields.Titles == nil || len(posting.Content.Fields.Locales) == 0 || byURL[posting.URL] != nil {
			return nil, ErrConfiguration
		}
		urls = append(urls, posting.URL)
		byURL[posting.URL] = posting.Content
	}
	for attempt := 1; attempt <= 3; attempt++ {
		result := &GreenhouseRichBatchResult{}
		_, err = a.Write(ctx, claim, false, func(ctx context.Context, tx pgx.Tx) error {
			rows, err := tx.Query(ctx, richMonitorDiffSQL, urls, profile.BoardID, true)
			if err != nil {
				return err
			}
			type classified struct{ action, id, url string }
			classifiedRows := make([]classified, 0, len(batch))
			seen := make(map[string]bool, len(batch))
			for rows.Next() {
				var action, url string
				var id *string
				var descriptionHash *int64
				var needsScrape bool
				if err := rows.Scan(&action, &id, &url, &descriptionHash, &needsScrape); err != nil {
					rows.Close()
					return err
				}
				if byURL[url] == nil || seen[url] || needsScrape {
					rows.Close()
					return errors.New("rich monitor classification lost input identity")
				}
				seen[url] = true
				classifiedRows = append(classifiedRows, classified{action, optionalID(id), url})
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				return err
			}
			if len(classifiedRows) != len(batch) {
				return errors.New("rich monitor classification lost input identity")
			}
			for _, row := range classifiedRows {
				content := byURL[row.url]
				switch row.action {
				case "foreign":
					// An active global duplicate supplies liveness only. The first
					// owner retains its content and company/board attribution.
					result.Foreign++
					continue
				case "new":
					fields := content.Fields
					err := tx.QueryRow(ctx, richMonitorInsertSQL, profile.CompanyID, profile.BoardID,
						fields.EmploymentType, row.url, fields.Titles, fields.Locales,
						fields.LocationIDs, fields.LocationTypes,
						fields.SalaryMin, fields.SalaryMax, fields.SalaryCurrency, fields.SalaryPeriod, fields.SalaryEUR,
						fields.ExperienceMin, fields.ExperienceMax, fields.TechnologyIDs,
						fields.OccupationID, fields.SeniorityID).Scan(&row.id)
					if errors.Is(err, pgx.ErrNoRows) {
						result.Deduplicated++
						continue
					}
					if err != nil {
						return err
					}
					result.Inserted++
				case "touched", "relisted", "foreign_relisted":
					if !canonicalUUID.MatchString(row.id) {
						return errors.New("rich monitor classification lost posting identity")
					}
					if err := refreshRichMonitorContent(ctx, tx, row.id, content.Fields); err != nil {
						return err
					}
					switch row.action {
					case "touched":
						result.Touched++
					case "relisted":
						result.Relisted++
					case "foreign_relisted":
						result.ForeignRelisted++
					}
				default:
					return errors.New("unknown rich monitor classification")
				}
				if content.Description != nil {
					if _, err := saveRichMonitorDescription(ctx, tx, row.id, content.Description); err != nil {
						return err
					}
				}
			}
			return nil
		})
		if err == nil {
			return result, nil
		}
		// PostgreSQL's deadlock abort proves the entire callback rolled back.
		// Preserve the Python diff's three-attempt, 50/100 ms jitter budget.
		// Never retry timeout, cancellation or an ambiguous commit observation.
		var failure *pgconn.PgError
		if !errors.As(err, &failure) || failure.Code != "40P01" || attempt == 3 {
			return nil, err
		}
		ceiling := 50 * time.Millisecond * time.Duration(1<<(attempt-1))
		delay := ceiling/2 + time.Duration(rand.Float64()*float64(ceiling/2))
		timer := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
	return nil, ErrObservation
}

func optionalID(id *string) string {
	if id == nil {
		return ""
	}
	return *id
}

func refreshRichMonitorContent(ctx context.Context, tx pgx.Tx, postingID string, f GreenhouseRichFields) error {
	updated, err := tx.Exec(ctx, refreshRichMonitorSQL, postingID, f.EmploymentType, f.Titles, f.Locales,
		f.LocationIDs, f.LocationTypes, f.TechnologyIDs,
		f.SalaryMin, f.SalaryMax, f.SalaryCurrency, f.SalaryPeriod, f.SalaryEUR,
		f.ExperienceMin, f.ExperienceMax, f.OccupationID, f.SeniorityID)
	if err == nil && updated.RowsAffected() != 1 {
		return errors.New("rich monitor posting unavailable")
	}
	return err
}

func saveRichMonitorDescription(ctx context.Context, tx pgx.Tx, postingID string, description *GreenhouseRichDescription) (bool, error) {
	// Recompute the detached staged hash before it crosses the persistence
	// boundary. Description equality is decided by PostgreSQL's exact bytes.
	if description.HTML == "" || description.Locale == "" || !utf8.ValidString(description.HTML) || !utf8.ValidString(description.Locale) || strings.ContainsRune(description.HTML, 0) || strings.ContainsRune(description.Locale, 0) {
		return false, ErrConfiguration
	}
	digest := sha256.Sum256([]byte(description.HTML))
	if int64(binary.BigEndian.Uint64(digest[:8])) != description.Hash {
		return false, ErrConfiguration
	}
	var sampled, existed, scheduled, changed bool
	var before, after *string
	err := tx.QueryRow(ctx, richMonitorDescriptionSQL, postingID, description.Locale, description.HTML, description.Hash, description.Hash).Scan(
		&sampled, &existed, &before, &after, &scheduled, &changed)
	return scheduled, err
}

// Same expressions as queries.monitor._BATCH_UPDATE_RICH_CONTENT, with
// positional parameters in place of Python's COPY temporary table.
const refreshRichMonitorSQL = `
UPDATE job_posting
SET employment_type = $2,
    titles = $3, locales = COALESCE($4, locales),
    location_ids = $5, location_types = $6,
    technology_ids = COALESCE($7, technology_ids),
    salary_min = $8, salary_max = $9,
    salary_currency = $10, salary_period = $11, salary_eur = $12,
    experience_min = CASE WHEN $13::numeric IS NULL AND $14::numeric IS NULL
        THEN experience_min ELSE $13 END,
    experience_max = CASE WHEN $13::numeric IS NULL AND $14::numeric IS NULL
        THEN experience_max ELSE $14 END,
    occupation_id = COALESCE($15, occupation_id),
    seniority_id = COALESCE($16, seniority_id)
WHERE id = $1
`
