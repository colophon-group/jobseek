package executor

import (
	"context"
	"errors"
	"net"
	"time"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
	"github.com/jackc/pgx/v5"
)

type Executor struct {
	Store     *Store
	Processor *Processor
	Shard     string
	Epoch     int64
}

func (s *Store) ReadEnrichSnapshot(ctx context.Context, postingID string) (*EnrichSnapshot, error) {
	if !canonicalUUID.MatchString(postingID) {
		return nil, ErrProtocol
	}
	var locales, locationTypes []string
	result := &EnrichSnapshot{}
	err := s.pool.QueryRow(ctx, fetchPostingForEnrichSQL, postingID).Scan(&result.Titles, &locales, &result.LocationIDs, &locationTypes, &result.EmploymentType)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	return result, err
}

// Handle owns the existing authorization/commit conversation. The supervisor
// keeps its Redis lease mutex held until this handler acknowledges the durable
// database schedule; this process has no renderer or Redis authority.
func (e *Executor) Handle(ctx context.Context, conn *net.UnixConn, request Request) error {
	if e == nil || e.Store == nil || e.Processor == nil || e.Shard != "lightpanda-b0" {
		return ErrProtocol
	}
	if err := request.validateTask(e.Shard, e.Epoch); err != nil {
		return err
	}
	if err := conn.SetDeadline(time.Now().Add(AuthorizationTimeout)); err != nil {
		return err
	}
	if err := WriteMessage(conn, map[string]any{"type": "authorize", "claim_token": request.ClaimToken, "lease_until_ms": request.LeaseUntilMS}); err != nil {
		return err
	}
	frame, err := ReadFrame(conn)
	if err != nil {
		return err
	}
	lease, err := DecodeAuthorization(frame, request.ClaimToken, request.LeaseUntilMS)
	if err != nil {
		return err
	}
	commit, cancel := context.WithTimeout(ctx, CommitTimeout)
	defer cancel()
	if err := conn.SetDeadline(time.Now().Add(CommitTimeout)); err != nil {
		return err
	}
	fence := Fence{PostingID: request.Task.Envelope.TaskID, ShardID: e.Shard, RoutingEpoch: e.Epoch, ConfigRevision: request.Task.Envelope.ConfigRevision, PayloadSHA256: request.PayloadSHA256, ClaimToken: request.ClaimToken}
	if err := e.Store.Activate(commit, fence); err != nil {
		return err
	}
	detail, err := e.Store.ReadCurrentDetail(commit, request.Task)
	if err != nil {
		return err
	}
	if detail != nil && detail.Schedulable {
		if err := e.process(commit, fence, request); err != nil {
			return err
		}
	}
	schedule, err := e.Store.ReadSchedule(commit, fence.PostingID)
	if err != nil {
		return err
	}
	response := map[string]any{"type": "committed", "claim_token": request.ClaimToken, "lease_until_ms": lease}
	if schedule != nil && schedule.IsActive && schedule.NextScrapeAt != nil {
		response["next_ready_at_ms"] = schedule.NextScrapeAt.UnixMilli()
	}
	return WriteMessage(conn, response)
}

func (e *Executor) process(ctx context.Context, fence Fence, request Request) error {
	reserved, err := e.Store.PostingReserved(ctx, fence.PostingID)
	if err == nil && reserved {
		return nil
	}
	var prepared *PreparedContent
	if err == nil {
		var content map[string]any
		content, err = ParseRendered(request.Task, request.Result)
		if err == nil {
			value, failure := b0task.ParseCanonicalValue(request.Task.Envelope.ParserConfig)
			if failure != nil {
				return ErrProtocol
			}
			config, ok := value.(map[string]any)
			if !ok {
				return ErrProtocol
			}
			var existing *EnrichSnapshot
			if _, enrich := selectedEnrichment(config); enrich {
				existing, err = e.Store.ReadEnrichSnapshot(ctx, fence.PostingID)
			}
			if err == nil {
				prepared, err = e.Processor.Prepare(ctx, content, config, existing)
			}
		}
	}
	if err != nil {
		if errors.Is(err, ErrAuthorityLost) {
			return err
		}
		return e.Store.AuthoritativeWrite(ctx, fence, func(tx pgx.Tx) error { return RecordFailure(ctx, tx, fence.PostingID, FailureClass(err)) })
	}
	err = e.Store.AuthoritativeWrite(ctx, fence, func(tx pgx.Tx) error {
		var err error
		if prepared.Enrich {
			_, err = SaveEnrichment(ctx, tx, fence.PostingID, prepared.Fields, prepared.Description)
		} else {
			_, err = SaveContent(ctx, tx, fence.PostingID, prepared.Fields, prepared.Description)
		}
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, recordSuccessSQL, fence.PostingID)
		return err
	})
	if err == nil || errors.Is(err, ErrAuthorityLost) {
		return err
	}
	// Python records persistence failures after the attempted content
	// transaction rolls back. Cancellation still prevents this second write;
	// authority rejection must never become a retry-budget mutation.
	return e.Store.AuthoritativeWrite(ctx, fence, func(tx pgx.Tx) error { return RecordFailure(ctx, tx, fence.PostingID, FailureClass(err)) })
}
