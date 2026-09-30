package executor

import (
	"context"
	"errors"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
)

type CurrentDetail struct {
	DescriptionHash     *int64
	ScrapeIntervalHours int64
	Schedulable         bool
}

// ReadCurrentDetail rereads mutable PostgreSQL identity after fence activation.
// Deleted and unschedulable tasks do not acquire a new parser assignment.
func (s *Store) ReadCurrentDetail(ctx context.Context, task b0task.Task) (*CurrentDetail, error) {
	var boardID, sourceURL string
	var hash *int64
	var active, enabled, browser *bool
	var due *time.Time
	var slug, status, crawlerType *string
	var metadata []byte
	var interval *int64
	err := s.pool.QueryRow(ctx, currentDetailSQL, task.Envelope.TaskID).Scan(&boardID, &sourceURL, &hash, &active, &due, &slug, &enabled, &status, &metadata, &crawlerType, &browser, &interval)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if active == nil || !*active || due == nil {
		return &CurrentDetail{ScrapeIntervalHours: 1}, nil
	}
	value, err := b0task.ParseCanonicalValue(metadata)
	if err != nil {
		return nil, ErrProtocol
	}
	object, ok := value.(map[string]any)
	if !ok || object["scraper_type"] != task.Envelope.ScraperType {
		return nil, ErrProtocol
	}
	_, assignment, err := b0task.ResolveAssignment(string(metadata))
	if err != nil {
		return nil, ErrProtocol
	}
	e := task.Envelope
	if boardID != e.BoardID || sourceURL != e.SourceURL || enabled == nil || !*enabled || status == nil || *status != "active" || browser == nil || !*browser ||
		assignment.Digest != e.AssignmentDigestSHA256 || assignment.ScraperType != e.ScraperType || assignment.RoutingRevision != e.RoutingRevision || assignment.TimeoutMS != e.TimeoutMS || assignment.Wait != e.Wait || !b0task.SameOptionalString(assignment.WaitFallback, e.WaitFallback) {
		return nil, ErrProtocol
	}
	hours := int64(24)
	if interval != nil {
		hours = *interval
	}
	if hours < 1 || hours > 8760 {
		return nil, ErrProtocol
	}
	return &CurrentDetail{DescriptionHash: hash, ScrapeIntervalHours: hours, Schedulable: true}, nil
}
