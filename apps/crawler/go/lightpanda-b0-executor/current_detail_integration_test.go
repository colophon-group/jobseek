package executor

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"testing"

	b0task "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/b0task"
)

func TestPostgresCurrentDetailRejectsMutableIdentityDrift(t *testing.T) {
	store, f, board := fixture(t)
	ctx := context.Background()
	request, err := DecodeRequest(protocolFixture(t)["request"])
	if err != nil {
		t.Fatal(err)
	}
	original, err := b0task.DecodeCanonical(request.TaskPayload, request.PayloadSHA256, b0task.Route{ShardID: "lightpanda-b0", RoutingEpoch: 7, EngineOwner: "go"})
	if err != nil {
		t.Fatal(err)
	}
	e := original.Envelope
	e.TaskID, e.BoardID, e.SourceURL, e.Domain, e.RoutingEpoch = f.PostingID, board, "https://native-executor.invalid/"+f.PostingID, "native-executor.invalid", f.RoutingEpoch
	canonical, err := b0task.CanonicalJSON(e, false)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(canonical)
	task, err := b0task.DecodeCanonical(string(canonical), hex.EncodeToString(digest[:]), b0task.Route{ShardID: f.ShardID, RoutingEpoch: f.RoutingEpoch, EngineOwner: "go"})
	if err != nil {
		t.Fatal(err)
	}
	metadata, err := json.Marshal(map[string]any{"scraper_type": "json-ld", "scraper_config": e.ParserConfig})
	if err != nil {
		t.Fatal(err)
	}
	reset := func() {
		t.Helper()
		if _, err := store.pool.Exec(ctx, "UPDATE job_board SET metadata=$2::jsonb,scraper_needs_browser=true,is_enabled=true,board_status='active',scrape_interval_hours=12 WHERE id=$1", board, metadata); err != nil {
			t.Fatal(err)
		}
		if _, err := store.pool.Exec(ctx, "UPDATE job_posting SET source_url=$2,is_active=true,next_scrape_at=now(),description_r2_hash=123 WHERE id=$1", f.PostingID, e.SourceURL); err != nil {
			t.Fatal(err)
		}
	}
	reset()
	current, err := store.ReadCurrentDetail(ctx, task)
	if err != nil || current == nil || !current.Schedulable || current.DescriptionHash == nil || *current.DescriptionHash != 123 || current.ScrapeIntervalHours != 12 {
		t.Fatalf("current detail mismatch: %+v %v", current, err)
	}
	for name, query := range map[string]string{
		"source":       "UPDATE job_posting SET source_url='https://native-executor.invalid/changed' WHERE id=$1",
		"disabled":     "UPDATE job_board SET is_enabled=false WHERE id=$1",
		"browser":      "UPDATE job_board SET scraper_needs_browser=false WHERE id=$1",
		"parser":       "UPDATE job_board SET metadata=jsonb_set(metadata,'{scraper_config,routing_revision}','\"changed\"'::jsonb) WHERE id=$1",
		"missing_type": "UPDATE job_board SET metadata=metadata-'scraper_type' WHERE id=$1",
		"interval":     "UPDATE job_board SET scrape_interval_hours=9000 WHERE id=$1",
	} {
		t.Run(name, func(t *testing.T) {
			reset()
			id := board
			if name == "source" {
				id = f.PostingID
			}
			if _, err := store.pool.Exec(ctx, query, id); err != nil {
				t.Fatal(err)
			}
			if _, err := store.ReadCurrentDetail(ctx, task); !errors.Is(err, ErrProtocol) {
				t.Fatalf("accepted changed identity: %v", err)
			}
		})
	}
	for name, query := range map[string]string{
		"inactive":    "UPDATE job_posting SET is_active=false WHERE id=$1",
		"unscheduled": "UPDATE job_posting SET next_scrape_at=NULL WHERE id=$1",
	} {
		t.Run(name, func(t *testing.T) {
			reset()
			if _, err := store.pool.Exec(ctx, query, f.PostingID); err != nil {
				t.Fatal(err)
			}
			current, err := store.ReadCurrentDetail(ctx, task)
			if err != nil || current == nil || current.Schedulable || current.DescriptionHash != nil || current.ScrapeIntervalHours != 1 {
				t.Fatalf("unschedulable mismatch: %+v %v", current, err)
			}
		})
	}
	if _, err := store.pool.Exec(ctx, "DELETE FROM job_posting WHERE id=$1", f.PostingID); err != nil {
		t.Fatal(err)
	}
	if current, err := store.ReadCurrentDetail(ctx, task); err != nil || current != nil {
		t.Fatalf("deleted task mismatch: %+v %v", current, err)
	}
}
