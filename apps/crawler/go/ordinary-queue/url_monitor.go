package queue

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// URLOnlyDetail is a separately scheduled detail scrape of a canonical posting.
// It carries no claim or write authority. A monitor never fills detail fields.
type URLOnlyDetail struct {
	ID, BoardID, URL string
	DescriptionHash  *int64
	Due              time.Time
	Browser          bool
}

type URLOnlyBatchResult struct {
	Inserted, Touched, Relisted, Foreign, ForeignRelisted, Deduplicated int
	Details                                                             []URLOnlyDetail
}

// The existing Python URL-only insert, paired with the same globally ordered
// diff used by rich monitors. The source_url unique constraint preserves the
// first owner's company and board when discoveries race.
const urlOnlyInsertSQL = `INSERT INTO job_posting
 (company_id,board_id,source_url,first_seen_at,last_seen_at,next_scrape_at,is_active,titles,locales)
 SELECT $1::uuid,$2::uuid,u.url,now(),now(),now(),true,'{}','{}'
 FROM unnest($3::text[]) AS u(url)
 ON CONFLICT(source_url) DO NOTHING RETURNING id::text,source_url`

// WriteURLOnlyBatch persists one bounded chunk under an installed monitor's
// unchanged canonical/Redis/PG fence. It leaves absence detection, scheduling
// the board and settlement to its cycle. Detail intents are returned only after
// commit; a missing-content touch repairs a lost enqueue on the next inventory.
func (a *Authority) WriteURLOnlyBatch(ctx context.Context, claim *Claim, urls []string) (*URLOnlyBatchResult, error) {
	if !a.valid(claim) || a.ownership == nil || claim.task.Kind != Monitor || len(urls) < 1 || len(urls) > 500 {
		return nil, ErrConfiguration
	}
	seen := make(map[string]bool, len(urls))
	for _, raw := range urls {
		u, err := url.Parse(raw)
		if err != nil || len(raw) > 8192 || !utf8.ValidString(raw) || strings.ContainsRune(raw, 0) || u.User != nil || (u.Scheme != "https" && u.Scheme != "http") || u.Hostname() == "" || seen[raw] {
			return nil, ErrConfiguration
		}
		seen[raw] = true
	}
	var result *URLOnlyBatchResult
	for attempt := 1; attempt <= 3; attempt++ {
		result = &URLOnlyBatchResult{}
		_, err := a.Write(ctx, claim, false, func(ctx context.Context, tx pgx.Tx) error {
			var reserved bool
			var company string
			if err := tx.QueryRow(ctx, "SELECT company_id::text,tdm_reserved FROM job_board WHERE id=$1::uuid", claim.task.ID).Scan(&company, &reserved); err != nil {
				return err
			}
			if reserved {
				return ErrPublisherReserved
			}
			rows, err := tx.Query(ctx, richMonitorDiffSQL, urls, claim.task.ID, false)
			if err != nil {
				return err
			}
			type classified struct {
				action, id, raw string
				hash            *int64
				enqueue         bool
			}
			items := make([]classified, 0, len(urls))
			classifiedURLs := map[string]bool{}
			for rows.Next() {
				var item classified
				var id *string
				if err := rows.Scan(&item.action, &id, &item.raw, &item.hash, &item.enqueue); err != nil {
					rows.Close()
					return err
				}
				if !seen[item.raw] || classifiedURLs[item.raw] {
					rows.Close()
					return ErrProtocol
				}
				classifiedURLs[item.raw] = true
				item.id = optionalID(id)
				items = append(items, item)
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				return err
			}
			if len(items) != len(urls) {
				return ErrProtocol
			}
			newURLs := []string{}
			detailIDs := []string{}
			for _, item := range items {
				switch item.action {
				case "new":
					newURLs = append(newURLs, item.raw)
				case "foreign":
					result.Foreign++
				case "touched", "relisted", "foreign_relisted":
					if !canonicalUUID.MatchString(item.id) {
						return ErrProtocol
					}
					switch item.action {
					case "touched":
						result.Touched++
					case "relisted":
						result.Relisted++
					case "foreign_relisted":
						result.ForeignRelisted++
					}
					if item.enqueue || item.action != "touched" {
						detailIDs = append(detailIDs, item.id)
					}
				default:
					return ErrProtocol
				}
			}
			if len(newURLs) > 0 {
				rows, err := tx.Query(ctx, urlOnlyInsertSQL, company, claim.task.ID, newURLs)
				if err != nil {
					return err
				}
				for rows.Next() {
					var id, raw string
					if err := rows.Scan(&id, &raw); err != nil {
						rows.Close()
						return err
					}
					if !seen[raw] || !canonicalUUID.MatchString(id) {
						rows.Close()
						return ErrProtocol
					}
					detailIDs = append(detailIDs, id)
					result.Inserted++
				}
				err = rows.Err()
				rows.Close()
				if err != nil {
					return err
				}
				result.Deduplicated = len(newURLs) - result.Inserted
			}
			if len(detailIDs) == 0 {
				return nil
			}
			// Foreign relists keep the canonical board's detail routing. A board
			// reservation prevents a new detail intent without transferring owner.
			rows, err = tx.Query(ctx, `SELECT p.id::text,p.board_id::text,p.source_url,p.description_r2_hash,p.next_scrape_at,b.scraper_needs_browser
 FROM job_posting p JOIN job_board b ON b.id=p.board_id
 WHERE p.id=ANY($1::uuid[]) AND p.is_active AND p.next_scrape_at IS NOT NULL AND NOT b.tdm_reserved
 ORDER BY p.id`, detailIDs)
			if err != nil {
				return err
			}
			for rows.Next() {
				var d URLOnlyDetail
				if err := rows.Scan(&d.ID, &d.BoardID, &d.URL, &d.DescriptionHash, &d.Due, &d.Browser); err != nil {
					rows.Close()
					return err
				}
				result.Details = append(result.Details, d)
			}
			err = rows.Err()
			rows.Close()
			return err
		})
		if err == nil {
			return result, nil
		}
		if !ordinaryDeadlock(err) || attempt == 3 {
			return nil, err
		}
		ceiling := 50 * time.Millisecond * time.Duration(1<<(attempt-1))
		if err := waitURLDiffRetry(ctx, ceiling/2+time.Duration(rand.Float64()*float64(ceiling/2))); err != nil {
			return nil, err
		}
	}
	return nil, ErrObservation
}

func ordinaryDeadlock(err error) bool {
	var e interface{ SQLState() string }
	return errors.As(err, &e) && e.SQLState() == "40P01"
}
func waitURLDiffRetry(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func (c *GreenhouseCycle) WriteURLOnlyBatch(ctx context.Context, urls []string) (*URLOnlyBatchResult, error) {
	if c == nil || c.authority == nil {
		return nil, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done || c.failed {
		return nil, ErrConfiguration
	}
	result, err := c.authority.WriteURLOnlyBatch(ctx, c.claim, urls)
	if err != nil {
		c.failed = true
		return nil, err
	}
	c.processed += len(urls)
	for _, raw := range urls {
		c.identities[raw] = true
	}
	return result, nil
}
