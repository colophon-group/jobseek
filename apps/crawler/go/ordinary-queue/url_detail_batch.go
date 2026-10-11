package queue

import (
	"context"
	"encoding/json"
	"time"

	"github.com/jackc/pgx/v5"
)

const URLDetailPublicationBatch = 64

type urlDetailIntent struct {
	Ordinal int       `json:"ordinal"`
	ID      string    `json:"id"`
	BoardID string    `json:"board_id"`
	URL     string    `json:"url"`
	Due     time.Time `json:"due"`
	Hash    *int64    `json:"hash"`
}

// EnqueueURLDetails shares one bounded write-fence transaction for committed
// intents. Its lease/epoch/board locks span every original Lua publication;
// neither ownership nor canonical posting state is cached across transactions.
// Stale intents are omitted, exactly as in EnqueueURLDetail. A failed later
// publication retains the earlier valid prefix for the existing repair path.
func (c *GreenhouseCycle) EnqueueURLDetails(ctx context.Context, details []URLOnlyDetail) (int, error) {
	if c == nil || c.authority == nil || len(details) == 0 || len(details) > URLDetailPublicationBatch {
		return 0, ErrConfiguration
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.done || c.failed {
		return 0, ErrConfiguration
	}
	intents := make([]urlDetailIntent, len(details))
	for i, d := range details {
		if !c.identities[d.URL] || !canonicalUUID.MatchString(d.ID) || !canonicalUUID.MatchString(d.BoardID) {
			return 0, ErrConfiguration
		}
		intents[i] = urlDetailIntent{i, d.ID, d.BoardID, d.URL, d.Due, d.DescriptionHash}
	}
	body, err := json.Marshal(intents)
	if err != nil {
		return 0, ErrConfiguration
	}
	added := 0
	_, err = c.authority.Write(ctx, c.claim, false, func(ctx context.Context, tx pgx.Tx) error {
		var reserved bool
		if err := tx.QueryRow(ctx, "SELECT tdm_reserved FROM job_board WHERE id=$1::uuid", c.claim.task.ID).Scan(&reserved); err != nil {
			return err
		}
		if reserved {
			return ErrPublisherReserved
		}
		rows, err := tx.Query(ctx, `SELECT p.id::text,p.board_id::text,p.source_url,p.description_r2_hash,p.next_scrape_at,b.scraper_needs_browser
 FROM jsonb_to_recordset($1::jsonb) AS i(ordinal integer,id uuid,board_id uuid,url text,due timestamptz,hash bigint)
 JOIN job_posting p ON p.id=i.id AND p.board_id=i.board_id AND p.source_url=i.url
 JOIN job_board b ON b.id=p.board_id
 WHERE p.is_active AND NOT b.tdm_reserved AND p.next_scrape_at=i.due
 AND p.description_r2_hash IS NOT DISTINCT FROM i.hash
 ORDER BY i.ordinal FOR SHARE OF p,b`, string(body))
		if err != nil {
			return err
		}
		current := make([]URLOnlyDetail, 0, len(details))
		for rows.Next() {
			var d URLOnlyDetail
			if err := rows.Scan(&d.ID, &d.BoardID, &d.URL, &d.DescriptionHash, &d.Due, &d.Browser); err != nil {
				rows.Close()
				return err
			}
			current = append(current, d)
		}
		err = rows.Err()
		rows.Close()
		if err != nil {
			return err
		}
		if len(current) > len(details) {
			return ErrObservation
		}
		for _, d := range current {
			ok, err := c.authority.queue.EnqueueURLDetail(ctx, d)
			if err != nil {
				return err
			}
			if ok {
				added++
			}
		}
		return nil
	})
	if err != nil {
		c.failed = true
		return added, err
	}
	return added, nil
}
